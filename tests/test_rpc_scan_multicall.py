"""Pure multicall encoding/decoding round-trips."""
import pytest
from eth_abi import decode as abi_decode, encode as abi_encode

from cerebro_mcp.rpc_scan.multicall import (
    SEL_AGGREGATE3,
    coerce_arg,
    decode_aggregate3,
    decode_outputs,
    encode_aggregate3,
    encode_call,
    parse_signature,
    selector,
)

ADDR = "0x" + "ab" * 20


def test_parse_signature_with_outputs():
    assert parse_signature("balanceOf(address)(uint256)") == (
        "balanceOf", ["address"], ["uint256"],
    )


def test_parse_signature_no_inputs_no_outputs():
    assert parse_signature("getOwners()") == ("getOwners", [], [])
    assert parse_signature("getOwners()(address[])") == ("getOwners", [], ["address[]"])


def test_parse_signature_multi_arg():
    assert parse_signature("getModulesPaginated(address,uint256)(address[],address)") == (
        "getModulesPaginated", ["address", "uint256"], ["address[]", "address"],
    )


def test_parse_signature_malformed():
    with pytest.raises(ValueError, match="Malformed"):
        parse_signature("balanceOf")
    with pytest.raises(ValueError, match="Malformed"):
        parse_signature("(address)(uint256)")


def test_parse_signature_tuple_rejected():
    with pytest.raises(ValueError, match="Tuple-typed"):
        parse_signature("quote((address,uint256))(uint256)")


def test_known_selector_balance_of():
    assert selector("balanceOf", ["address"]).hex() == "70a08231"


def test_coerce_arg():
    assert coerce_arg("uint256", "123") == 123
    assert coerce_arg("uint256", "0xff") == 255
    assert coerce_arg("address", ADDR) == ADDR
    assert coerce_arg("bool", 1) is True
    assert coerce_arg("bytes32", "0x" + "11" * 32) == b"\x11" * 32
    with pytest.raises(ValueError, match="address arg"):
        coerce_arg("address", 5)


def test_encode_call_arg_count_mismatch():
    sel = selector("balanceOf", ["address"])
    with pytest.raises(ValueError, match="argument count mismatch"):
        encode_call(sel, ["address"], [])


def test_encode_aggregate3_has_selector_and_roundtrips():
    calldata = encode_call(selector("balanceOf", ["address"]), ["address"], [ADDR])
    payload = encode_aggregate3([(ADDR, True, calldata)])
    assert payload.startswith("0x" + SEL_AGGREGATE3.hex())
    decoded = abi_decode(
        ["(address,bool,bytes)[]"], bytes.fromhex(payload[2:])[4:]
    )[0]
    assert decoded[0][0].lower() == ADDR
    assert decoded[0][1] is True
    assert bytes(decoded[0][2]) == calldata


def test_decode_aggregate3_roundtrip():
    raw = "0x" + abi_encode(
        ["(bool,bytes)[]"],
        [[(True, abi_encode(["uint256"], [42])), (False, b"")]],
    ).hex()
    results = decode_aggregate3(raw)
    assert results[0][0] is True
    assert decode_outputs(["uint256"], results[0][1]) == [42]
    assert results[1] == (False, b"")


def test_decode_outputs_never_raises():
    assert decode_outputs(["uint256"], b"\x01") is None  # malformed
    assert decode_outputs([], b"\x01") is None           # no declared types
    assert decode_outputs(["uint256"], b"") is None      # empty return
