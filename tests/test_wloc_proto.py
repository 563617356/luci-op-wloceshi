"""wloc_proto 单元测试。

用构造的合成 protobuf 帧验证解析/改写/回退行为，
不依赖真实 Apple 回包，也不依赖 mitmproxy，可直接在 PC 上跑。

运行：python3 tests/test_wloc_proto.py
"""

import gzip
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "root", "usr", "libexec", "wloc"))

from wloc_proto import (  # noqa: E402
    NoPatchNeeded,
    Stats,
    WlocParseError,
    encode_field,
    encode_varint,
    is_gzip,
    parse_fields,
    patch_payload,
    patch_wloc_body,
    read_varint,
)

FAILURES = []
PASSED = 0


def check(condition, label):
    global PASSED
    if condition:
        PASSED += 1
        print("  ok   %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL %s" % label)


def check_raises(fn, label):
    try:
        fn()
    except WlocParseError:
        check(True, label)
    except Exception as exc:  # noqa: BLE001
        check(False, "%s (raised %r, expected WlocParseError)" % (label, exc))
    else:
        check(False, "%s (no exception raised)" % label)


# ---------------------------------------------------------------- 构造工具

def make_position(lat, lon, accuracy=25):
    """构造一个定位条目：field1=纬度 field2=经度 field3=精度。"""
    return (
        encode_field(1, 0, round(1e8 * lat))
        + encode_field(2, 0, round(1e8 * lon))
        + encode_field(3, 0, accuracy)
    )


def make_wifi_cell(mac, lat, lon, accuracy=25):
    """构造 WiFi 条目：field1=MAC field2=定位数据。"""
    return encode_field(1, 2, mac.encode("ascii")) + encode_field(2, 2, make_position(lat, lon, accuracy))


def make_cell(lat, lon, accuracy=25):
    """构造基站条目：field5=定位数据。"""
    return encode_field(5, 2, make_position(lat, lon, accuracy))


def make_payload(lat, lon, accuracy=25):
    """构造顶层载荷：field2=WiFi field22=基站 field7=无关字段。"""
    return (
        encode_field(7, 0, 12345)
        + encode_field(2, 2, make_wifi_cell("aa:bb:cc:dd:ee:ff", lat, lon, accuracy))
        + encode_field(22, 2, make_cell(lat, lon, accuracy))
    )


def make_body(lat, lon, base=8, accuracy=25, tail=None):
    """构造完整回包，匹配引擎的 base 语义。

    引擎（及原版 wloc.js）的帧约定：
        header   = body[:base+8]
        长度字段 = body[base+8 : base+10]  大端 uint16
        payload  = body[base+10 : base+10+len]
        tail     = 剩余

    所以要让引擎在 base 处成功，body 必须在 [base+8:base+10] 放真实长度，
    payload 从 base+10 起。这里 header 填 base+8 字节 0xFF：
    作为 protobuf tag 表示 wire type 7（保留），非法，
    确保偏移错位时解析必然失败、不会误判成功。
    """
    payload = make_payload(lat, lon, accuracy)
    body_payload = encode_field(9, 0, 1) + payload + encode_field(10, 0, 2)
    if tail is None:
        # 帧尾同样必须是合法 protobuf。field 0 在 protobuf 中非法，
        # 不能用 0x00 填充——否则正确帧解析也会失败。
        tail = encode_field(11, 0, 3)

    header = b"\xff" * (base + 8)
    return header + struct.pack(">H", len(body_payload)) + body_payload + tail


def strip_frame(body, base=8):
    """剥掉帧头，只留 payload。整包前面是帧头，不是合法 protobuf 起始。"""
    frame_len = struct.unpack(">H", body[base + 8:base + 10])[0]
    return body[base + 10:base + 10 + frame_len]


def extract_positions(buf, depth=0, found=None):
    """递归找出所有定位条目 (纬度, 经度)，用于验证改写结果。"""
    if found is None:
        found = []
    if depth > 5:
        return found
    try:
        fields = parse_fields(buf)
    except WlocParseError:
        return found

    has1 = any(f[0] == 1 and f[1] == 0 for f in fields)
    has2 = any(f[0] == 2 and f[1] == 0 for f in fields)
    if has1 and has2:
        lat_v = next(f[2] for f in fields if f[0] == 1 and f[1] == 0)
        lon_v = next(f[2] for f in fields if f[0] == 2 and f[1] == 0)
        found.append((lat_v / 1e8, lon_v / 1e8))
        return found

    for _fno, ftype, value, _raw in fields:
        if ftype == 2:
            extract_positions(value, depth + 1, found)
    return found


def find_accuracy(buf, depth=0):
    """找出第一个定位条目的精度值（field 3）。"""
    if depth > 5:
        return None
    try:
        fields = parse_fields(buf)
    except WlocParseError:
        return None

    has1 = any(f[0] == 1 and f[1] == 0 for f in fields)
    has2 = any(f[0] == 2 and f[1] == 0 for f in fields)
    if has1 and has2:
        for fno, ftype, value, _raw in fields:
            if fno == 3 and ftype == 0:
                return value
        return None

    for _fno, ftype, value, _raw in fields:
        if ftype == 2:
            got = find_accuracy(value, depth + 1)
            if got is not None:
                return got
    return None


# ---------------------------------------------------------------- varint

def test_varint():
    print("varint")
    for value in (0, 1, 127, 128, 300, 16383, 16384, 1139411400, 2254467700):
        encoded = encode_varint(value)
        decoded, pos = read_varint(encoded, 0)
        check(decoded == value and pos == len(encoded), "roundtrip %d" % value)

    check_raises(lambda: read_varint(b"\x80\x80", 0), "truncated varint raises")
    check_raises(lambda: read_varint(b"\x80" * 12, 0), "overlong varint raises")
    for value in (-1, -33, -180, -1234567890):
        decoded, pos = read_varint(encode_varint(value), 0)
        check(decoded == value and pos == 10, "negative roundtrip %d (len=%d)" % (value, pos))


# ---------------------------------------------------------------- protobuf

def test_protobuf_basics():
    print("protobuf basics")
    check(is_gzip(b"\x1f\x8b\x08\x00"), "gzip magic detected")
    check(not is_gzip(b"\x08\x00"), "non-gzip rejected")
    check(not is_gzip(b"\x1f"), "short buffer not gzip")

    fields = parse_fields(encode_field(1, 0, 42) + encode_field(2, 2, b"hi"))
    check(len(fields) == 2, "parsed two fields")
    check(fields[0][0] == 1 and fields[0][2] == 42, "varint field value")
    check(fields[1][0] == 2 and fields[1][2] == b"hi", "bytes field value")
    check(fields[1][3] == encode_field(2, 2, b"hi"), "raw slice preserved")

    check_raises(lambda: parse_fields(encode_varint(0)), "field number 0 rejected")
    check_raises(lambda: parse_fields(encode_varint((1 << 3) | 3)), "wire type 3 rejected")
    check_raises(lambda: parse_fields(encode_field(1, 2, b"")[:-1] + b"\x7f"), "overrun rejected")


# ---------------------------------------------------------------- 改写

def test_patch_basic():
    print("patch basic")
    body = make_body(22.544577, 113.94114)
    patched, stats = patch_wloc_body(body, 39.9042, 116.4074, 30)

    positions = extract_positions(strip_frame(patched))
    check(len(positions) >= 2, "found positions in patched body (%d)" % len(positions))
    all_match = bool(positions) and all(
        abs(lat - 39.9042) < 1e-6 and abs(lon - 116.4074) < 1e-6 for lat, lon in positions
    )
    check(all_match, "all positions rewritten to target")
    check(stats.locations == 2, "locations counter = %d" % stats.locations)
    check(stats.wifi == 1, "wifi counter = %d" % stats.wifi)
    check(stats.cell == 1, "cell counter = %d" % stats.cell)
    check(stats.skipped == 0, "skipped counter = 0")

    base = 8
    frame_len = struct.unpack(">H", patched[base + 8:base + 10])[0]
    tail_len = len(patched) - base - 10 - frame_len
    check(frame_len > 0 and tail_len > 0,
          "frame length rewritten consistently (len=%d tail=%d)" % (frame_len, tail_len))


def test_accuracy():
    print("accuracy field")
    body = make_body(22.544577, 113.94114, accuracy=25)
    patched, _stats = patch_wloc_body(body, 1.0, 2.0, 88)
    check(find_accuracy(strip_frame(patched)) == 88, "accuracy rewritten to 88")


def test_negative_and_zero():
    print("edge coordinates")
    # 目标与原值完全一致 -> 属于 NoPatchNeeded，不是错误
    body = make_body(22.5, 113.9)
    try:
        patch_wloc_body(body, 22.5, 113.9, 25)
        check(False, "identical target raises NoPatchNeeded")
    except NoPatchNeeded:
        check(True, "identical target raises NoPatchNeeded")
    except WlocParseError as exc:
        check(False, "identical target raises NoPatchNeeded (got %r)" % exc)

    # 南半球：源坐标与目标坐标不同，应正常改写
    south = make_body(22.544577, 113.94114)
    patched_s, _ = patch_wloc_body(south, -33.8688, 151.2093, 25)
    positions = extract_positions(strip_frame(patched_s))
    check(
        bool(positions) and all(
            abs(lat + 33.8688) < 1e-6 and abs(lon - 151.2093) < 1e-6 for lat, lon in positions
        ),
        "southern hemisphere coordinates handled",
    )

    # 坐标 0,0
    zero = make_body(22.544577, 113.94114)
    patched_z, _ = patch_wloc_body(zero, 0.0, 0.0, 25)
    positions_z = extract_positions(strip_frame(patched_z))
    check(
        bool(positions_z) and all(
            abs(lat) < 1e-9 and abs(lon) < 1e-9 for lat, lon in positions_z
        ),
        "zero coordinates encoded as varint 0",
    )


# ---------------------------------------------------------------- 边界

def test_no_patchable():
    print("no patchable payload")
    check_raises(lambda: patch_wloc_body(b"\x00" * 4, 1.0, 2.0, 25), "too-short body raises")
    check_raises(lambda: patch_wloc_body(b"", 1.0, 2.0, 25), "empty body raises")

    garbage = bytes(range(256)) * 2
    try:
        patch_wloc_body(garbage, 1.0, 2.0, 25)
        check(False, "garbage body raises (should not find payload)")
    except WlocParseError:
        check(True, "garbage body raises (no false positive)")


def test_unrelated_fields_untouched():
    print("unrelated data untouched")
    body = make_body(22.544577, 113.94114)
    payload = strip_frame(body)

    patched, _ = patch_wloc_body(body, 39.9042, 116.4074, 30)
    new_payload = strip_frame(patched)

    orig_fields = parse_fields(payload)
    new_fields = parse_fields(new_payload)
    check(len(orig_fields) == len(new_fields), "field count unchanged")

    orig_f7 = [f[3] for f in orig_fields if f[0] == 7]
    new_f7 = [f[3] for f in new_fields if f[0] == 7]
    check(bool(orig_f7) and orig_f7 == new_f7, "unrelated field 7 byte-identical")

    wifi = [f[2] for f in orig_fields if f[0] == 2][0]
    orig_macs = [f[2] for f in parse_fields(wifi) if f[0] == 1]
    check(orig_macs == [b"aa:bb:cc:dd:ee:ff"], "MAC address preserved")

    cell = [f[2] for f in orig_fields if f[0] == 22][0]
    new_cell = [f[2] for f in new_fields if f[0] == 22][0]
    check(
        [f[3] for f in parse_fields(cell) if f[0] == 5][0][:2]
        == [f[3] for f in parse_fields(new_cell) if f[0] == 5][0][:2],
        "cell field tag+length prefix intact",
    )


def test_mac_guard():
    print("MAC guard")
    not_mac = encode_field(1, 2, b"not-a-mac-address") + encode_field(
        2, 2, make_position(1.0, 2.0)
    )
    payload = encode_field(2, 2, not_mac)
    stats = Stats()
    result = patch_payload(payload, 50.0, 60.0, 25, stats)
    check(result == payload, "non-MAC field 1 prevents rewrite")
    check(stats.locations == 0, "no location counted when guard fails")


def test_offsets():
    print("frame offset scan")
    for base in (0, 2, 4, 8, 16):
        body = make_body(22.5, 113.9, base=base)
        try:
            patched, stats = patch_wloc_body(body, 10.0, 20.0, 25)
            positions = extract_positions(strip_frame(patched, base))
            check(
                bool(positions) and all(abs(lat - 10.0) < 1e-6 for lat, _ in positions),
                "patched with base=%d" % base,
            )
        except (WlocParseError, NoPatchNeeded) as exc:
            check(False, "patched with base=%d (%s)" % (base, exc))


def test_gzip_roundtrip():
    print("gzip")
    body = make_body(22.544577, 113.94114)
    compressed = gzip.compress(body)
    check(is_gzip(compressed), "compressed body detected as gzip")
    decompressed = gzip.decompress(compressed)
    patched, _ = patch_wloc_body(decompressed, 31.2, 121.5, 40)
    positions = extract_positions(strip_frame(patched))
    check(
        bool(positions) and all(
            abs(lat - 31.2) < 1e-6 and abs(lon - 121.5) < 1e-6 for lat, lon in positions
        ),
        "patch works on decompressed gzip body",
    )


def test_idempotent():
    print("idempotency")
    body = make_body(22.5, 113.9)
    once, stats = patch_wloc_body(body, 40.0, 116.0, 25)
    check(stats.locations == 2, "first patch rewrote 2 positions")

    # 再次改写到相同坐标：引擎应识别为"无需改动"而不是失败
    try:
        twice, _ = patch_wloc_body(once, 40.0, 116.0, 25)
        check(False, "re-patch to same target raises NoPatchNeeded")
    except NoPatchNeeded:
        check(True, "re-patch to same target raises NoPatchNeeded (idempotent)")

    # 改到新坐标应正常工作
    third, _stats = patch_wloc_body(once, 41.0, 117.0, 25)
    positions = extract_positions(strip_frame(third))
    check(
        bool(positions) and all(abs(lat - 41.0) < 1e-6 for lat, _ in positions),
        "re-patch to new target works",
    )


def test_large_body():
    print("large body")
    cells = b"".join(
        encode_field(2, 2, make_wifi_cell("aa:bb:cc:dd:ee:%02x" % i, 22.5, 113.9))
        for i in range(256)
    )
    body = b"\xff" * 16 + struct.pack(">H", len(cells)) + cells
    patched, stats = patch_wloc_body(body, 35.0, 139.0, 25)
    check(stats.wifi == 256, "all 256 wifi cells patched (%d)" % stats.wifi)
    check(stats.locations == 256, "all 256 positions patched (%d)" % stats.locations)
    # 定点数长度稳定，整体长度通常不变；关键是帧长度字段与实际 payload 一致
    new_len = struct.unpack(">H", patched[16:18])[0]
    check(new_len == len(patched) - 18, "frame length field consistent for large body")
    positions = extract_positions(strip_frame(patched, base=8))
    check(
        len(positions) == 256 and all(abs(lat - 35.0) < 1e-6 for lat, _ in positions),
        "all 256 positions carry target coordinates",
    )


def main():
    print("=" * 62)
    print("wloc_proto unit tests")
    print("=" * 62)
    for fn in (
        test_varint,
        test_protobuf_basics,
        test_patch_basic,
        test_accuracy,
        test_negative_and_zero,
        test_no_patchable,
        test_unrelated_fields_untouched,
        test_mac_guard,
        test_offsets,
        test_gzip_roundtrip,
        test_idempotent,
        test_large_body,
    ):
        fn()
        print("")

    print("=" * 62)
    if FAILURES:
        print("FAILED %d / %d" % (len(FAILURES), PASSED + len(FAILURES)))
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("ALL %d CHECKS PASSED" % PASSED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
