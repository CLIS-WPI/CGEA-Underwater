"""Minimal hop-by-hop governance send and DATA queue reservation."""

from __future__ import annotations

import simpy

from cgea.network import GOVERNANCE_TX_TYPES, Packet, PacketType, UnderwaterNetwork


class _FakeSample:
    def __init__(self, ok=True, delay=0.01, rate=1e6, psp=1.0):
        self.link_available = ok
        self.propagation_delay_s = delay
        self.estimated_rate_bps = rate
        self.packet_success_probability = psp


class _FakeTrace:
    def sample_at(self, tx, rx, t):
        forbidden = {("a", "c"), ("c", "a")}
        if (tx, rx) in forbidden:
            return _FakeSample(ok=False, psp=0.0, rate=0.0)
        return _FakeSample()


def test_send_routed_uses_two_hops():
    env = simpy.Environment()
    net = UnderwaterNetwork(env, ["a", "b", "c"], "c", _FakeTrace(), queue_limit=8)

    def go():
        pkt = Packet(
            packet_id="d1",
            src="a",
            dst="c",
            ptype=PacketType.DIGEST,
            size_bytes=32,
            created_at=0.0,
        )
        yield net.send_routed(pkt)

    env.process(go())
    env.run(until=5)
    finals = [
        d
        for d in net.deliveries
        if d.success and d.packet.dst == "c" and (d.packet.origin or d.packet.src) == "a"
    ]
    assert finals
    hops = [d for d in net.deliveries if d.success]
    assert any(d.packet.dst == "b" for d in hops)
    assert PacketType.DIGEST in GOVERNANCE_TX_TYPES


def test_data_cannot_fill_reserved_governance_slots():
    env = simpy.Environment()
    net = UnderwaterNetwork(
        env, ["a", "b"], "b", _FakeTrace(), queue_limit=4, governance_reserved_queue_slots=2
    )
    # Slow the link so DATA stays queued.
    class Slow(_FakeTrace):
        def sample_at(self, tx, rx, t):
            s = _FakeSample(delay=10.0, rate=8.0, psp=1.0)
            return s

    net.trace = Slow()

    def flood():
        for i in range(6):
            net.send(
                Packet(
                    packet_id=f"data-{i}",
                    src="a",
                    dst="b",
                    ptype=PacketType.DATA,
                    size_bytes=64,
                    created_at=env.now,
                )
            )
        yield env.timeout(0.0)

    env.process(flood())
    env.run(until=0.001)
    data_drops = [d for d in net.deliveries if d.reason == "queue_full"]
    assert data_drops
    qlen = len(net.nodes["a"].queue)
    assert qlen <= 4 - 2


def test_paper_capsule_forbids_hard_safety_classes():
    from cgea.governance import PAPER_FORBIDDEN_ACTIONS, issue_capsule
    from cgea.mission import ActionType

    cap = issue_capsule("auv_00", "m1", 0.0)
    for a in PAPER_FORBIDDEN_ACTIONS:
        assert a in cap.forbidden_actions
    assert ActionType.REASSIGN_ANOTHER_AUV.value in cap.conditional_actions
    assert ActionType.ENTER_EXCLUSION_ZONE.value not in cap.allowed_actions
