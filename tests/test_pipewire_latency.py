"""Read-only PipeWire latency parsing through an injected command boundary."""

from collections.abc import Sequence

from lyriflux.domain.synchronization import AudioLatencyProbeStatus
from lyriflux.infrastructure.audio.pipewire import CommandResult, PipeWireLatencyProbe


def test_pipewire_latency_range_is_reported_but_not_auto_applied() -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: Sequence[str]) -> CommandResult:
        commands.append(tuple(command))
        if command[0] == "wpctl":
            return CommandResult(
                0,
                "id 55, type PipeWire:Interface:Node\n"
                '  * node.description = "Built-in Audio"\n'
                '  * node.latency = "1024/48000"\n',
                "",
            )
        return CommandResult(
            0,
            "Prop minQuantum\n  Float 0.0\n"
            "Prop maxQuantum\n  Float 1.0\n"
            "Prop minRate\n  Int 64\n"
            "Prop maxRate\n  Int 128\n"
            "Prop minNs\n  Long 1000000\n"
            "Prop maxNs\n  Long 2000000\n",
            "",
        )

    result = PipeWireLatencyProbe(run).probe()

    assert commands == [
        ("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"),
        ("pw-cli", "enum-params", "55", "Latency"),
    ]
    assert result.status is AudioLatencyProbeStatus.REPORTED
    assert result.route == "Built-in Audio"
    assert result.graph_quantum_us == 21_333
    assert result.minimum_us == 2_333
    assert result.maximum_us == 26_000
    assert result.safe_for_automatic_compensation is False
    assert result.diagnostics


def test_pipewire_failure_is_an_understandable_unavailable_state() -> None:
    result = PipeWireLatencyProbe(
        lambda _command: CommandResult(2, "", "Could not connect to PipeWire")
    ).probe()

    assert result.status is AudioLatencyProbeStatus.UNAVAILABLE
    assert result.minimum_us is None
    assert result.diagnostics == ("Could not connect to PipeWire",)


def test_graph_quantum_alone_is_partial_not_end_to_end_latency() -> None:
    def run(command: Sequence[str]) -> CommandResult:
        if command[0] == "wpctl":
            return CommandResult(
                0,
                'id 9, type PipeWire:Interface:Node\nnode.latency = "256/48000"',
                "",
            )
        return CommandResult(0, "Object without latency fields", "")

    result = PipeWireLatencyProbe(run).probe()

    assert result.status is AudioLatencyProbeStatus.PARTIAL
    assert result.graph_quantum_us == 5_333
    assert result.safe_for_automatic_compensation is False


def test_latency_ns_is_reported_when_sink_has_no_node_latency_property() -> None:
    def run(command: Sequence[str]) -> CommandResult:
        if command[0] == "wpctl":
            return CommandResult(
                0,
                "id 65, type PipeWire:Interface:Node\n"
                'node.description = "Synthetic Wireless Sink"\n'
                'node.name = "bluez_output.stable.1"',
                "",
            )
        return CommandResult(
            0,
            "Prop minQuantum\n  Float 0.0\n"
            "Prop maxQuantum\n  Float 0.0\n"
            "Prop minRate\n  Int 0\n"
            "Prop maxRate\n  Int 0\n"
            "Prop minNs\n  Long 139125000\n"
            "Prop maxNs\n  Long 139125000\n",
            "",
        )

    result = PipeWireLatencyProbe(run, lambda: 123).probe()

    assert result.status is AudioLatencyProbeStatus.REPORTED
    assert result.minimum_us == 139_125
    assert result.maximum_us == 139_125
    assert result.chosen_estimate_us == 139_125
    assert result.uncertainty_us == 0
    assert result.graph_quantum_us is None
    assert result.device_key is not None
    assert "bluez_output" not in result.device_key
    assert result.measured_at_ns == 123


def test_negative_external_latency_is_controlled_partial_evidence() -> None:
    def run(command: Sequence[str]) -> CommandResult:
        if command[0] == "wpctl":
            return CommandResult(
                0,
                'id 7, type PipeWire:Interface:Node\nnode.name = "bad-device"',
                "",
            )
        return CommandResult(
            0,
            "Prop minNs\n  Long -1000\nProp maxNs\n  Long -1000\n",
            "",
        )

    result = PipeWireLatencyProbe(run).probe()

    assert result.status is AudioLatencyProbeStatus.PARTIAL
    assert result.chosen_estimate_us is None
    assert "no usable range" in result.diagnostics[0]
