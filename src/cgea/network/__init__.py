"""SimPy discrete-event underwater network."""

from __future__ import annotations

import enum
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

import networkx as nx
import simpy

from cgea.acoustic.trace import ChannelTrace, LinkSample
from cgea.types import CgeaBaseModel


class PacketType(str, enum.Enum):
    DATA = "data"
    SUPERVISOR = "supervisor"
    AUTHORITY = "authority"
    DIGEST = "digest"
    PROVENANCE = "provenance"
    RECONCILE = "reconcile"


GOVERNANCE_TX_TYPES = {
    PacketType.AUTHORITY,
    PacketType.DIGEST,
    PacketType.PROVENANCE,
    PacketType.RECONCILE,
    PacketType.SUPERVISOR,
}


def stable_bernoulli(packet_id: str) -> float:
    """Process-stable U[0,1) from packet_id. Not Python's salted hash()."""
    digest = hashlib.sha256(packet_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class Packet(CgeaBaseModel):
    packet_id: str
    src: str
    dst: str
    ptype: PacketType
    size_bytes: int
    payload: dict[str, Any] = {}
    created_at: float = 0.0
    hops: list[str] = []
    # Logical endpoints when src/dst are the current physical hop.
    origin: str | None = None
    final_dst: str | None = None


@dataclass
class DeliveryRecord:
    packet: Packet
    delivered_at: float
    success: bool
    reason: str = ""


@dataclass
class NodeCommState:
    node_id: str
    queue: deque = field(default_factory=deque)
    bytes_tx: int = 0
    bytes_rx: int = 0
    packets_tx: int = 0
    packets_rx: int = 0
    packets_dropped: int = 0


class UnderwaterNetwork:
    """Repeatable communication behavior over replayed ChannelTrace.

    Features: propagation delay, queues, packet loss, bandwidth limits,
    link outages, asymmetric links, dynamic topology, partitions,
    surface-gateway reachability. No fancy MAC.
    """

    def __init__(
        self,
        env: simpy.Environment,
        node_ids: list[str],
        gateway_id: str,
        trace: ChannelTrace,
        queue_limit: int = 32,
        header_bytes: int = 32,
        outage_override: dict[tuple[str, str], bool] | None = None,
        governance_reserved_queue_slots: int = 4,
    ):
        self.env = env
        self.node_ids = list(node_ids)
        self.gateway_id = gateway_id
        self.trace = trace
        self.queue_limit = queue_limit
        self.header_bytes = header_bytes
        self.outage_override = outage_override or {}
        # DATA may not occupy these last slots; governance/recon may. Not a MAC redesign.
        self.governance_reserved_queue_slots = max(0, int(governance_reserved_queue_slots))
        self.nodes = {n: NodeCommState(node_id=n) for n in node_ids}
        self.inbox: dict[str, list[Packet]] = defaultdict(list)
        self.deliveries: list[DeliveryRecord] = []
        self.bytes_tx_by_ptype: dict[str, int] = defaultdict(int)
        self._handlers: dict[str, Callable[[Packet], None]] = {}
        self._forced_partition_groups: list[set[str]] | None = None

    def register_handler(self, node_id: str, handler: Callable[[Packet], None]) -> None:
        self._handlers[node_id] = handler

    def force_partitions(self, groups: list[set[str]] | None) -> None:
        """Intentionally partition the network (Phase 13)."""
        self._forced_partition_groups = groups

    def _same_partition(self, a: str, b: str) -> bool:
        if self._forced_partition_groups is None:
            return True
        for g in self._forced_partition_groups:
            if a in g and b in g:
                return True
        return False

    def link_sample(self, tx: str, rx: str, t: float | None = None) -> LinkSample | None:
        t = self.env.now if t is None else t
        return self.trace.sample_at(tx, rx, t)

    def link_available(self, tx: str, rx: str, t: float | None = None) -> bool:
        if (tx, rx) in self.outage_override:
            return self.outage_override[(tx, rx)]
        if not self._same_partition(tx, rx):
            return False
        sample = self.link_sample(tx, rx, t)
        return bool(sample and sample.link_available)

    def connectivity_graph(self, t: float | None = None) -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_nodes_from(self.node_ids)
        t = self.env.now if t is None else t
        for tx in self.node_ids:
            for rx in self.node_ids:
                if tx == rx:
                    continue
                if self.link_available(tx, rx, t):
                    sample = self.link_sample(tx, rx, t)
                    delay = sample.propagation_delay_s if sample else 1.0
                    rate = sample.estimated_rate_bps if sample else 0.0
                    g.add_edge(tx, rx, delay=delay, rate=rate)
        return g

    def partitions(self, t: float | None = None) -> list[set[str]]:
        g = self.connectivity_graph(t).to_undirected()
        return [set(c) for c in nx.connected_components(g)]

    def gateway_reachable(self, node_id: str, t: float | None = None) -> bool:
        g = self.connectivity_graph(t)
        if node_id == self.gateway_id:
            return True
        return nx.has_path(g, node_id, self.gateway_id)

    def _drop(self, packet: Packet, reason: str) -> None:
        self.nodes[packet.src].packets_dropped += 1
        self.deliveries.append(
            DeliveryRecord(packet=packet, delivered_at=self.env.now, success=False, reason=reason)
        )

    def _queue_accepts(self, packet: Packet) -> bool:
        qlen = len(self.nodes[packet.src].queue)
        if packet.ptype in GOVERNANCE_TX_TYPES:
            return qlen < self.queue_limit
        reserved = min(self.governance_reserved_queue_slots, self.queue_limit)
        return qlen < self.queue_limit - reserved

    def _enqueue(self, packet: Packet) -> None:
        q = self.nodes[packet.src].queue
        if packet.ptype in GOVERNANCE_TX_TYPES and q:
            # Behind the in-flight head; ahead of waiting DATA.
            q.insert(1, packet)
        else:
            q.append(packet)

    def _dequeue(self, packet: Packet) -> None:
        q = self.nodes[packet.src].queue
        try:
            q.remove(packet)
        except ValueError:
            pass

    def send(self, packet: Packet) -> simpy.events.Event:
        if packet.ptype in GOVERNANCE_TX_TYPES:
            return self.send_routed(packet)
        return self.env.process(self._send_hop_proc(packet))

    def send_routed(self, packet: Packet) -> simpy.events.Event:
        """Hop-by-hop along connectivity_graph shortest path. Same PHY/queue/loss per hop."""
        return self.env.process(self._send_routed_proc(packet))

    def _send_routed_proc(self, packet: Packet):
        origin = packet.origin or packet.src
        final_dst = packet.final_dst or packet.dst
        current = packet.src
        hop_i = 0
        while current != final_dst:
            g = self.connectivity_graph()
            try:
                path = nx.shortest_path(g, current, final_dst)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                self._drop(
                    packet.model_copy(
                        update={"src": current, "dst": final_dst, "origin": origin, "final_dst": final_dst}
                    ),
                    "no_path",
                )
                return
            if len(path) < 2:
                self._drop(
                    packet.model_copy(
                        update={"src": current, "dst": final_dst, "origin": origin, "final_dst": final_dst}
                    ),
                    "no_path",
                )
                return
            nxt = path[1]
            hop = packet.model_copy(
                update={
                    "src": current,
                    "dst": nxt,
                    "origin": origin,
                    "final_dst": final_dst,
                    "packet_id": f"{packet.packet_id}-h{hop_i}",
                    "hops": list(packet.hops),
                }
            )
            yield from self._send_hop_proc(hop)
            last = self.deliveries[-1]
            if not last.success:
                return
            current = nxt
            hop_i += 1
            packet.hops = list(hop.hops)

    def _send_hop_proc(self, packet: Packet):
        src_state = self.nodes[packet.src]
        if not self._queue_accepts(packet):
            self._drop(packet, "queue_full")
            return

        self._enqueue(packet)
        # Simple FIFO: wait transmission time then propagate
        sample = self.link_sample(packet.src, packet.dst)
        if sample is None or not self.link_available(packet.src, packet.dst):
            self._dequeue(packet)
            self._drop(packet, "link_down")
            return

        rate = max(sample.estimated_rate_bps, 1.0)
        tx_time = (packet.size_bytes * 8.0) / rate
        yield self.env.timeout(tx_time)
        self._dequeue(packet)

        src_state.bytes_tx += packet.size_bytes
        src_state.packets_tx += 1
        self.bytes_tx_by_ptype[packet.ptype.value] += packet.size_bytes

        # Propagation
        yield self.env.timeout(sample.propagation_delay_s)

        psp = sample.packet_success_probability
        success = stable_bernoulli(packet.packet_id) < psp
        if not success:
            src_state.packets_dropped += 1
            self.deliveries.append(
                DeliveryRecord(packet=packet, delivered_at=self.env.now, success=False, reason="erasure")
            )
            return

        dst_state = self.nodes[packet.dst]
        dst_state.bytes_rx += packet.size_bytes
        dst_state.packets_rx += 1
        packet.hops = list(packet.hops) + [packet.dst]
        self.inbox[packet.dst].append(packet)
        self.deliveries.append(
            DeliveryRecord(packet=packet, delivered_at=self.env.now, success=True, reason="ok")
        )
        handler = self._handlers.get(packet.dst)
        if handler:
            handler(packet)

    def broadcast(self, src: str, ptype: PacketType, payload: dict, size_bytes: int = 64) -> list:
        events = []
        for dst in self.node_ids:
            if dst == src:
                continue
            if not self.link_available(src, dst):
                continue
            pkt = Packet(
                packet_id=f"{src}-{dst}-{self.env.now}-{ptype.value}",
                src=src,
                dst=dst,
                ptype=ptype,
                size_bytes=size_bytes,
                payload=payload,
                created_at=self.env.now,
            )
            events.append(self.send(pkt))
        return events

    def total_bytes(self) -> dict[str, int]:
        tx = sum(n.bytes_tx for n in self.nodes.values())
        rx = sum(n.bytes_rx for n in self.nodes.values())
        gov_tx = sum(self.bytes_tx_by_ptype.get(t.value, 0) for t in GOVERNANCE_TX_TYPES)
        return {
            "tx": tx,
            "rx": rx,
            "total": tx,
            "governance_tx": gov_tx,
            "data_tx": tx - gov_tx,
        }
