from protocol_parser.display_batch import DisplayBatcher, SegmentBatchAccumulator
from protocol_parser.display_format import (
    build_receive_color_segments,
    format_monitor_raw_line,
    format_receive_frame_item,
    format_receive_frame_line,
    format_receive_raw_items,
    normalize_monitor_display_line,
)
from protocol_parser import ParseResult


def test_format_receive_frame_line_builds_protocol_summary():
    result = ParseResult(
        product="demo",
        description="demo frame",
        cmd_code="0x01",
        cmd_name="测试命令",
        direction="request",
        fields=[
            {"type": "separator", "name": "", "text": ""},
            {"type": "uint8", "name": "温度", "text": "25"},
        ],
        raw_hex="01 02 03",
        checksum_ok=True,
    )
    line, color = format_receive_frame_line(result, 1.0, hex_format=True)
    assert "测试命令" in line
    assert "温度=25" in line
    assert color == "#0000CD"


def test_format_receive_frame_item_includes_color_segments():
    result = ParseResult(
        product="demo",
        description="demo frame",
        cmd_code="0x01",
        cmd_name="测试命令",
        direction="request",
        fields=[],
        raw_hex="01",
        checksum_ok=True,
    )
    item = format_receive_frame_item(result, 1.0, hex_format=True)
    assert item["kind"] == "frame"
    assert item["segments"]
    assert any("[RX]" in segment[0] or segment[0].startswith("[") for segment in item["segments"])


def test_build_receive_color_segments_splits_timestamp():
    segments = build_receive_color_segments("[12:00:00.000] [RX] Raw-HEX 01\n", "#0000CD")
    assert segments[0][0].startswith("[12:")
    assert segments[0][1] == "#2E86FF"


def test_format_monitor_raw_line_strips_rx_tag_from_payload():
    line = format_monitor_raw_line(b"[RX] hello", 1.0, hex_format=False)
    assert line.endswith(" hello\n")
    assert "[RX]" not in line
    assert "[TX]" not in line


def test_format_monitor_raw_line_skips_tx_payload():
    line = format_monitor_raw_line(b"[TX] ignored", 1.0, hex_format=False)
    assert line == ""


def test_format_receive_raw_items_hex_mode():
    items = format_receive_raw_items(b"\x01\x02", 2.0, hex_format=True)
    assert len(items) == 1
    assert "Raw-HEX" in items[0]["text"]
    assert items[0]["raw_bytes"] == b"\x01\x02"
    monitor_line = format_monitor_raw_line(b"\x01\x02", 2.0, hex_format=True)
    assert "Raw-HEX" not in monitor_line
    assert "Raw-ASCII" not in monitor_line
    assert monitor_line.startswith("[")
    assert items[0]["monitor_line"] == monitor_line


def test_normalize_monitor_display_line_strips_rx_prefix():
    line = normalize_monitor_display_line("[12:00:00.000] [RX] Raw-ASCII hello\n")
    assert line == "[12:00:00.000] hello\n"
    assert "[RX]" not in line


def test_format_receive_raw_items_ascii_mode_has_segments():
    items = format_receive_raw_items(b"[RX] hello", 2.0, hex_format=False)
    assert len(items) == 1
    assert items[0]["segments"]
    assert "monitor_line" in items[0]
    assert "[RX]" not in items[0]["monitor_line"]
    assert items[0]["monitor_line"].endswith(" hello\n")


def test_display_batcher_coalesces_until_flush():
    payloads: list[dict] = []

    batcher = DisplayBatcher(payloads.append, batch_ms=40.0, max_items=100)
    batcher.add({"text": "a\n", "color": None, "ts_colorize": False}, frame=True)
    assert payloads == []
    batcher.flush(force=True)
    assert len(payloads) == 1
    assert payloads[0]["frame_count"] == 1
    assert len(payloads[0]["lines"]) == 1


def test_segment_batch_accumulator_flushes_batches():
    emitted: list[list[list[tuple]]] = []

    batcher = SegmentBatchAccumulator(emitted.append, batch_ms=40.0, max_batches=10)
    batcher.add([("line", "#000", False, None)])
    batcher.flush(force=True)
    assert len(emitted) == 1
    assert len(emitted[0]) == 1
