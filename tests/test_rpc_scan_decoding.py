"""Event signature parsing and log decoding edge cases."""
import pytest

from cerebro_mcp.rpc_scan.decoding import (
    AmbiguousEventError,
    EventDecoder,
    pad_address_topic,
    parse_event_signature,
    parsed_event_from_abi,
)

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDR_A = "0x" + "aa" * 20
ADDR_B = "0x" + "bb" * 20


def _topic_addr(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:]


def test_full_form_signature_topic0_matches_erc20_transfer():
    parsed = parse_event_signature(
        "Transfer(address indexed from, address indexed to, uint256 value)"
    )
    assert parsed.topic0 == TRANSFER_TOPIC0
    assert [i.indexed for i in parsed.variants[0]] == [True, True, False]


def test_short_form_well_known_has_both_layout_variants():
    parsed = parse_event_signature("Transfer(address,address,uint256)")
    assert parsed.topic0 == TRANSFER_TOPIC0
    assert len(parsed.variants) == 2


def test_short_form_unknown_event_raises_teaching_error():
    with pytest.raises(AmbiguousEventError, match="indexed layout is ambiguous"):
        parse_event_signature("Foo(address,uint256)")


def test_decoder_resolves_erc20_vs_erc721_by_topic_count():
    decoder = EventDecoder(parse_event_signature("Transfer(address,address,uint256)"))

    # 3 topics -> ERC-20: value decoded from data
    args, err = decoder.decode(
        [TRANSFER_TOPIC0, _topic_addr(ADDR_A), _topic_addr(ADDR_B)],
        "0x" + hex(1234)[2:].rjust(64, "0"),
    )
    assert err == ""
    assert args == {"from": ADDR_A, "to": ADDR_B, "value": 1234}

    # 4 topics -> ERC-721: tokenId decoded from topic3
    args, err = decoder.decode(
        [TRANSFER_TOPIC0, _topic_addr(ADDR_A), _topic_addr(ADDR_B),
         "0x" + hex(7)[2:].rjust(64, "0")],
        "0x",
    )
    assert err == ""
    assert args == {"from": ADDR_A, "to": ADDR_B, "tokenId": 7}


def test_promoted_columns_are_union_of_variants():
    decoder = EventDecoder(parse_event_signature("Transfer(address,address,uint256)"))
    cols = decoder.promoted_columns()
    assert [(a, c) for a, c, _t in cols] == [
        ("from", "arg_from"), ("to", "arg_to"),
        ("value", "arg_value"), ("tokenId", "arg_tokenId"),
    ]


def test_dynamic_indexed_arg_stays_raw_hash():
    parsed = parse_event_signature("Named(string indexed name, uint256 value)")
    decoder = EventDecoder(parsed)
    cols = decoder.promoted_columns()
    assert cols[0][1] == "arg_name_hash"  # column flags the hash semantics
    raw_topic = "0x" + "cd" * 32
    args, err = decoder.decode(
        [parsed.topic0, raw_topic], "0x" + hex(5)[2:].rjust(64, "0")
    )
    assert err == ""
    assert args["name"] == raw_topic  # keccak hash, never abi-decoded
    assert args["value"] == 5


def test_abi_mode_decoder_does_not_promote():
    event_abi = {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"type": "address", "indexed": True, "name": "from"},
            {"type": "address", "indexed": True, "name": "to"},
            {"type": "uint256", "indexed": False, "name": "value"},
        ],
    }
    decoder = EventDecoder(parsed_event_from_abi(event_abi), promote=False)
    assert decoder.promote is False
    assert decoder.topic0 == TRANSFER_TOPIC0


def test_decode_never_raises_on_garbage():
    decoder = EventDecoder(parse_event_signature("Transfer(address,address,uint256)"))
    args, err = decoder.decode([TRANSFER_TOPIC0, _topic_addr(ADDR_A), _topic_addr(ADDR_B)], "0xZZ")
    assert "decode_error" in err


def test_decode_unmatched_topic_count_reports_error():
    decoder = EventDecoder(
        parse_event_signature("ApprovalForAll(address,address,bool)")
    )
    args, err = decoder.decode(["0x00"], "0x")
    assert args == {} and "no layout variant" in err


def test_filter_layout_uses_variant_zero():
    decoder = EventDecoder(parse_event_signature("Transfer(address,address,uint256)"))
    assert decoder.filter_layout("to") == (True, 2)        # indexed, topics[2]
    assert decoder.filter_layout("value") == (True, None)  # ERC-20 layout: unindexed
    assert decoder.filter_layout("nope") == (False, None)


def test_signed_int_topic_decoding():
    parsed = parse_event_signature("Tick(int24 indexed tick)")
    decoder = EventDecoder(parsed)
    negative_one = "0x" + "f" * 64
    args, err = decoder.decode([parsed.topic0, negative_one], "0x")
    assert err == ""
    assert args["tick"] == -1


def test_pad_address_topic():
    assert pad_address_topic(ADDR_A) == "0x" + "00" * 12 + "aa" * 20


def test_tuple_signature_rejected():
    with pytest.raises(ValueError, match="Tuple-typed"):
        parse_event_signature("Swap((address,uint256) indexed pair)")
