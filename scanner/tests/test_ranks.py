"""Tests for the rank & account memory scanner.

Tests the IL2CPP memory navigation that reads player rank data and account
info from MTGA's memory by navigating:
  WrapperController → PlayerRankServiceWrapper → _combinedRankInfo
  WrapperController → AccountClient → AccountInformation

The scanner mirrors what mtgatool's Rust queries.rs ``ranks_from()`` and
``account_from()`` do for the Mono backend.
"""

from __future__ import annotations

import struct
import pytest

from scanner.il2cpp_nav import (
    IL2CPP_OFFSETS,
    MockMemory,
    find_class_by_name,
    get_class_fields,
    read_il2cpp_string,
)
from scanner.rank_scanner import (
    AccountInfo,
    FullRankScanResult,
    RankInfo,
    RankScanResult,
    RANK_CLASS_NAMES,
    format_account_summary,
    format_account_table,
    format_rank_summary,
    format_rank_table,
    rank_class_name,
    scan_account_from_instance,
    scan_ranks,
    scan_ranks_and_account,
    scan_ranks_from_instance,
)


# ---------------------------------------------------------------------------
# Helpers — build mock IL2CPP memory layouts (same patterns as test_il2cpp_nav)
# ---------------------------------------------------------------------------

def _pack_ptr(v: int) -> bytes:
    return struct.pack("<Q", v)


def _pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _pack_i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _make_il2cpp_string(text: str) -> bytes:
    """Build a fake IL2CPP string in memory.

    Layout:
      +0x00: class_ptr (8 bytes, not checked by reader)
      +0x10: length (i32, char count)
      +0x14: chars (UTF-16LE)
    """
    chars = text.encode("utf-16-le")
    raw = b"\x00" * 0x10  # header up to length
    raw += struct.pack("<i", len(text))
    raw += chars
    pad = (32 - len(raw) % 32) % 32
    raw += b"\x00" * pad
    return raw


def _make_field_info(name_ptr: int, offset: int, type_ptr: int = 0x200000) -> bytes:
    """Build a 32-byte FieldInfo structure.

    FieldInfo layout (IL2CPP, 32 bytes):
      +0x00: name_ptr (8 bytes)
      +0x08: type_ptr (8 bytes)
      +0x10: parent (8 bytes)
      +0x18: offset (u32) + token (u32)
    """
    return _pack_ptr(name_ptr) + _pack_ptr(type_ptr) + _pack_ptr(0) + _pack_u32(offset) + _pack_u32(0)


def _make_class(name_ptr: int, fields_ptr: int, field_count: int = 5) -> bytes:
    """Build a minimal IL2CPP class structure (512 bytes)."""
    raw = bytearray(0x200)
    # class_name at +0x10
    raw[0x10:0x18] = _pack_ptr(name_ptr)
    # class_namespace at +0x18
    raw[0x18:0x20] = _pack_ptr(0)
    # class_fields at +0x80
    raw[0x80:0x88] = _pack_ptr(fields_ptr)
    # class_field_count at +0x124
    raw[0x124:0x128] = _pack_u32(field_count)
    # class_static_fields at +0xA8
    raw[0xA8:0xB0] = _pack_ptr(0)
    return bytes(raw)


def _make_type_info_table(classes: list[tuple[int, int]]) -> bytes:
    """Build a TypeInfoTable: array of class pointers."""
    max_idx = max(idx for idx, _ in classes) + 1
    table = bytearray(max_idx * 8)
    for idx, addr in classes:
        table[idx * 8: idx * 8 + 8] = _pack_ptr(addr)
    return bytes(table)


def _write_string(mem: MockMemory, addr: int, text: str) -> None:
    """Write an IL2CPP string at addr."""
    mem.write(addr, _make_il2cpp_string(text))


def _write_ascii(mem: MockMemory, addr: int, text: str) -> None:
    """Write a null-terminated ASCII string at addr."""
    mem.write(addr, text.encode("ascii") + b"\x00")


def _setup_wrapper_controller(mem: MockMemory) -> dict[str, int]:
    """Set up a complete WrapperController mock in memory.

    Creates:
      - WrapperController class with PlayerRankServiceWrapper and AccountClient fields
      - PlayerRankServiceWrapper class with _combinedRankInfo field
      - _combinedRankInfo class with constructed/limited rank fields + playerId
      - AccountClient class with AccountInformation field
      - AccountInformation class with DisplayName, AccountID, etc.
      - All linked instances

    Returns a dict of named addresses for use in tests.
    """
    addrs: dict[str, int] = {}

    # -- Name strings for classes --
    wc_name = 0x100000
    _write_ascii(mem, wc_name, "WrapperController")
    prsw_name = 0x110000
    _write_ascii(mem, prsw_name, "PlayerRankServiceWrapper")
    cri_name = 0x120000
    _write_ascii(mem, cri_name, "CombinedRankInfo")
    ac_name = 0x130000
    _write_ascii(mem, ac_name, "AccountClient")
    ai_name = 0x140000
    _write_ascii(mem, ai_name, "AccountInformation")

    # -- Field name strings for WrapperController --
    wc_f1_name = 0x150000
    _write_ascii(mem, wc_f1_name, "<PlayerRankServiceWrapper>k__BackingField")
    wc_f2_name = 0x160000
    _write_ascii(mem, wc_f2_name, "<AccountClient>k__BackingField")

    # -- Field name strings for PlayerRankServiceWrapper --
    prsw_f1_name = 0x170000
    _write_ascii(mem, prsw_f1_name, "_combinedRankInfo")

    # -- Field name strings for CombinedRankInfo --
    cri_field_names_base = 0x180000
    cri_field_names = [
        "playerId",
        "constructedClass", "constructedLevel", "constructedStep",
        "constructedMatchesWon", "constructedMatchesLost", "constructedMatchesDrawn",
        "constructedSeasonOrdinal", "constructedPercentile", "constructedLeaderboardPlace",
        "limitedClass", "limitedLevel", "limitedStep",
        "limitedMatchesWon", "limitedMatchesLost", "limitedMatchesDrawn",
        "limitedSeasonOrdinal", "limitedPercentile", "limitedLeaderboardPlace",
    ]
    for i, name in enumerate(cri_field_names):
        _write_ascii(mem, cri_field_names_base + i * 0x100, name)

    # -- Field name strings for AccountClient --
    ac_f1_name = 0x200000
    _write_ascii(mem, ac_f1_name, "<AccountInformation>k__BackingField")

    # -- Field name strings for AccountInformation --
    ai_field_names_base = 0x210000
    ai_field_names = [
        "DisplayName", "AccountID", "PersonaID", "GameID",
        "Email", "ExternalID", "CountryCode", "AccessToken",
    ]
    for i, name in enumerate(ai_field_names):
        _write_ascii(mem, ai_field_names_base + i * 0x100, name)

    # -- Build WrapperController class --
    # Fields: PlayerRankServiceWrapper at offset 0x18, AccountClient at offset 0x20
    wc_fields_ptr = 0x300000
    wc_fields = _make_field_info(name_ptr=wc_f1_name, offset=0x18) + _make_field_info(name_ptr=wc_f2_name, offset=0x20)
    mem.write(wc_fields_ptr, wc_fields)
    wc_class = 0x310000
    mem.write(wc_class, _make_class(name_ptr=wc_name, fields_ptr=wc_fields_ptr, field_count=2))
    addrs["wc_class"] = wc_class

    # -- Build PlayerRankServiceWrapper class --
    prsw_fields_ptr = 0x320000
    prsw_fields = _make_field_info(name_ptr=prsw_f1_name, offset=0x18)
    mem.write(prsw_fields_ptr, prsw_fields)
    prsw_class = 0x330000
    mem.write(prsw_class, _make_class(name_ptr=prsw_name, fields_ptr=prsw_fields_ptr, field_count=1))

    # -- Build CombinedRankInfo class --
    # All fields use 8-byte spacing to avoid overlap (pointers are 8 bytes)
    cri_fields_ptr = 0x340000
    cri_fields = b""
    for i, name in enumerate(cri_field_names):
        name_ptr = cri_field_names_base + i * 0x100
        cri_fields += _make_field_info(name_ptr=name_ptr, offset=0x10 + i * 8)
    mem.write(cri_fields_ptr, cri_fields)
    cri_class = 0x350000
    mem.write(cri_class, _make_class(name_ptr=cri_name, fields_ptr=cri_fields_ptr, field_count=len(cri_field_names)))

    # -- Build AccountClient class --
    ac_fields_ptr = 0x360000
    ac_fields = _make_field_info(name_ptr=ac_f1_name, offset=0x18)
    mem.write(ac_fields_ptr, ac_fields)
    ac_class = 0x370000
    mem.write(ac_class, _make_class(name_ptr=ac_name, fields_ptr=ac_fields_ptr, field_count=1))

    # -- Build AccountInformation class --
    ai_fields_ptr = 0x380000
    ai_fields = b""
    for i, name in enumerate(ai_field_names):
        name_ptr = ai_field_names_base + i * 0x100
        ai_fields += _make_field_info(name_ptr=name_ptr, offset=0x10 + i * 8)
    mem.write(ai_fields_ptr, ai_fields)
    ai_class = 0x390000
    mem.write(ai_class, _make_class(name_ptr=ai_name, fields_ptr=ai_fields_ptr, field_count=len(ai_field_names)))

    # -- Build instances --
    # WrapperController instance
    wc_instance = 0x400000
    mem.write(wc_instance, _pack_ptr(wc_class))  # vtable → class
    addrs["wc_instance"] = wc_instance

    # PlayerRankServiceWrapper instance
    prsw_instance = 0x410000
    mem.write(prsw_instance, _pack_ptr(prsw_class))
    # Write _combinedRankInfo pointer at offset 0x18
    cri_instance = 0x420000
    mem.write(prsw_instance + 0x18, _pack_ptr(cri_instance))

    # CombinedRankInfo instance
    mem.write(cri_instance, _pack_ptr(cri_class))
    addrs["cri_instance"] = cri_instance

    # Write playerId string
    player_id_str = 0x430000
    _write_string(mem, player_id_str, "TEST-PLAYER-123")
    # playerId is first field (index 0), offset 0x10
    mem.write(cri_instance + 0x10, _pack_ptr(player_id_str))

    # Write constructed rank fields (8-byte spacing, starting at index 1 = offset 0x18)
    # constructedClass (u32) at 0x18 = 5 (Platinum)
    mem.write(cri_instance + 0x18, _pack_u32(5))
    # constructedLevel (i32) at 0x20 = 4
    mem.write(cri_instance + 0x20, _pack_i32(4))
    # constructedStep (i32) at 0x28 = 2
    mem.write(cri_instance + 0x28, _pack_i32(2))
    # constructedMatchesWon at 0x30 = 12
    mem.write(cri_instance + 0x30, _pack_i32(12))
    # constructedMatchesLost at 0x38 = 5
    mem.write(cri_instance + 0x38, _pack_i32(5))
    # constructedMatchesDrawn at 0x40 = 1
    mem.write(cri_instance + 0x40, _pack_i32(1))
    # constructedSeasonOrdinal at 0x48 = 45
    mem.write(cri_instance + 0x48, _pack_i32(45))
    # constructedPercentile (string ptr) at 0x50
    percentile_str = 0x440000
    _write_string(mem, percentile_str, "0.75")
    mem.write(cri_instance + 0x50, _pack_ptr(percentile_str))
    # constructedLeaderboardPlace at 0x58 = 1274
    mem.write(cri_instance + 0x58, _pack_i32(1274))

    # Write limited rank fields (starting at index 10 = offset 0x60)
    # limitedClass (u32) at 0x60 = 3 (Silver)
    mem.write(cri_instance + 0x60, _pack_u32(3))
    # limitedLevel at 0x68 = 1
    mem.write(cri_instance + 0x68, _pack_i32(1))
    # limitedStep at 0x70 = 0
    mem.write(cri_instance + 0x70, _pack_i32(0))
    # limitedMatchesWon at 0x78 = 3
    mem.write(cri_instance + 0x78, _pack_i32(3))
    # limitedMatchesLost at 0x80 = 2
    mem.write(cri_instance + 0x80, _pack_i32(2))
    # limitedMatchesDrawn at 0x88 = 0
    mem.write(cri_instance + 0x88, _pack_i32(0))
    # limitedSeasonOrdinal at 0x90 = 45
    mem.write(cri_instance + 0x90, _pack_i32(45))
    # limitedPercentile (string ptr) at 0x98
    limited_percentile_str = 0x450000
    _write_string(mem, limited_percentile_str, "0.42")
    mem.write(cri_instance + 0x98, _pack_ptr(limited_percentile_str))
    # limitedLeaderboardPlace at 0xA0 = 0
    mem.write(cri_instance + 0xA0, _pack_i32(0))

    # Write PlayerRankServiceWrapper pointer in WrapperController
    mem.write(wc_instance + 0x18, _pack_ptr(prsw_instance))

    # -- AccountClient instance --
    ac_instance = 0x460000
    mem.write(ac_instance, _pack_ptr(ac_class))
    # AccountInformation pointer at offset 0x18
    ai_instance = 0x470000
    mem.write(ac_instance + 0x18, _pack_ptr(ai_instance))

    # -- AccountInformation instance --
    mem.write(ai_instance, _pack_ptr(ai_class))
    addrs["ai_instance"] = ai_instance

    # Write account string fields
    account_values = {
        "DisplayName": "TestPlayer",
        "AccountID": "ACC-12345-67890",
        "PersonaID": "P-001",
        "GameID": "G-002",
        "Email": "test@example.com",
        "ExternalID": "EXT-003",
        "CountryCode": "DE",
        "AccessToken": "token-abc-def",
    }
    str_addr = 0x480000
    for i, (field_name, value) in enumerate(account_values.items()):
        s_addr = str_addr + i * 0x1000
        _write_string(mem, s_addr, value)
        # Field offset is 0x10 + i * 8
        mem.write(ai_instance + 0x10 + i * 8, _pack_ptr(s_addr))

    # Write AccountClient pointer in WrapperController
    mem.write(wc_instance + 0x20, _pack_ptr(ac_instance))

    return addrs


# ---------------------------------------------------------------------------
# rank_class_name tests
# ---------------------------------------------------------------------------

class TestRankClassName:
    """Tests for the rank_class_name mapping function."""

    def test_known_values(self):
        assert rank_class_name(0) == "None"
        assert rank_class_name(1) == "Spark"
        assert rank_class_name(2) == "Bronze"
        assert rank_class_name(3) == "Silver"
        assert rank_class_name(4) == "Gold"
        assert rank_class_name(5) == "Platinum"
        assert rank_class_name(6) == "Diamond"
        assert rank_class_name(7) == "Master"
        assert rank_class_name(8) == "Mythic"

    def test_unknown_value(self):
        assert rank_class_name(99) == "Unknown"
        assert rank_class_name(255) == "Unknown"

    def test_all_values_in_dict(self):
        for k, v in RANK_CLASS_NAMES.items():
            assert rank_class_name(k) == v


# ---------------------------------------------------------------------------
# RankInfo / AccountInfo dataclass tests
# ---------------------------------------------------------------------------

class TestRankInfoDataclass:
    """Tests for RankInfo dataclass."""

    def test_defaults(self):
        ri = RankInfo()
        assert ri.class_value == 0
        assert ri.class_name == "None"
        assert ri.level == 0
        assert ri.wins == 0
        assert ri.losses == 0
        assert ri.draws == 0
        assert ri.percentile == ""
        assert ri.leaderboard_place == 0

    def test_to_dict(self):
        ri = RankInfo(
            season_ordinal=10,
            class_value=5,
            class_name="Platinum",
            level=4,
            step=2,
            wins=12,
            losses=5,
            draws=1,
            percentile="0.75",
            leaderboard_place=1274,
        )
        d = ri.to_dict()
        assert d["seasonOrdinal"] == 10
        assert d["class"] == "Platinum"
        assert d["classValue"] == 5
        assert d["level"] == 4
        assert d["step"] == 2
        assert d["wins"] == 12
        assert d["losses"] == 5
        assert d["draws"] == 1
        assert d["percentile"] == "0.75"
        assert d["leaderboardPlace"] == 1274


class TestAccountInfoDataclass:
    """Tests for AccountInfo dataclass."""

    def test_defaults(self):
        ai = AccountInfo()
        assert ai.display_name == ""
        assert ai.account_id == ""
        assert ai.warnings == []

    def test_to_dict_excludes_access_token(self):
        ai = AccountInfo(
            display_name="TestPlayer",
            account_id="ACC-123",
            access_token="secret-token",
        )
        d = ai.to_dict()
        assert d["displayName"] == "TestPlayer"
        assert d["accountId"] == "ACC-123"
        assert "accessToken" not in d
        assert "access_token" not in d


# ---------------------------------------------------------------------------
# scan_ranks_from_instance tests
# ---------------------------------------------------------------------------

class TestScanRanksFromInstance:
    """Tests for reading rank data from a WrapperController instance."""

    def test_full_rank_scan(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.player_id == "TEST-PLAYER-123"
        assert result.constructed.class_value == 5
        assert result.constructed.class_name == "Platinum"
        assert result.constructed.level == 4
        assert result.constructed.step == 2
        assert result.constructed.wins == 12
        assert result.constructed.losses == 5
        assert result.constructed.draws == 1
        assert result.constructed.season_ordinal == 45
        assert result.constructed.percentile == "0.75"
        assert result.constructed.leaderboard_place == 1274

        assert result.limited.class_value == 3
        assert result.limited.class_name == "Silver"
        assert result.limited.level == 1
        assert result.limited.step == 0
        assert result.limited.wins == 3
        assert result.limited.losses == 2
        assert result.limited.draws == 0
        assert result.limited.season_ordinal == 45
        assert result.limited.percentile == "0.42"
        assert result.limited.leaderboard_place == 0

    def test_to_dict_structure(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])
        d = result.to_dict()

        assert d["playerId"] == "TEST-PLAYER-123"
        assert "constructed" in d
        assert "limited" in d
        assert d["constructed"]["class"] == "Platinum"
        assert d["limited"]["class"] == "Silver"

    def test_missing_rank_wrapper_field(self):
        """If WrapperController has no PlayerRankServiceWrapper field, return empty result with warning."""
        mem = MockMemory()
        # Build a WrapperController class with no relevant fields
        wc_name = 0x100000
        _write_ascii(mem, wc_name, "WrapperController")
        some_field_name = 0x110000
        _write_ascii(mem, some_field_name, "SomeOtherField")
        fields_ptr = 0x200000
        mem.write(fields_ptr, _make_field_info(name_ptr=some_field_name, offset=0x18))
        wc_class = 0x300000
        mem.write(wc_class, _make_class(name_ptr=wc_name, fields_ptr=fields_ptr, field_count=1))
        wc_instance = 0x400000
        mem.write(wc_instance, _pack_ptr(wc_class))

        result = scan_ranks_from_instance(mem, wc_instance, wc_class)

        assert result.player_id == ""
        assert result.constructed.class_value == 0
        assert len(result.warnings) > 0
        assert "PlayerRankServiceWrapper" in result.warnings[0]

    def test_null_rank_wrapper_pointer(self):
        """If PlayerRankServiceWrapper pointer is null, return with warning."""
        mem = MockMemory()
        # Standard setup but null out the pointer
        addrs = _setup_wrapper_controller(mem)
        mem.write(addrs["wc_instance"] + 0x18, _pack_ptr(0))

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.player_id == ""
        assert len(result.warnings) > 0
        assert "null" in result.warnings[0].lower() or "invalid" in result.warnings[0].lower()

    def test_null_combined_rank_info_pointer(self):
        """If _combinedRankInfo pointer is null, return with warning."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)
        # Null out the _combinedRankInfo pointer
        prsw_instance = 0x410000
        mem.write(prsw_instance + 0x18, _pack_ptr(0))

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.player_id == ""
        assert len(result.warnings) > 0

    def test_no_player_id_field(self):
        """If playerId field is missing, player_id should be empty but rank data still reads."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)
        # Overwrite playerId string pointer with null
        mem.write(addrs["cri_instance"] + 0x10, _pack_ptr(0))

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.player_id == ""
        # Rank data should still be present
        assert result.constructed.class_name == "Platinum"

    def test_raw_address_set(self):
        """raw_address should be set to the _combinedRankInfo instance address."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.raw_address == addrs["cri_instance"]


# ---------------------------------------------------------------------------
# scan_account_from_instance tests
# ---------------------------------------------------------------------------

class TestScanAccountFromInstance:
    """Tests for reading account info from a WrapperController instance."""

    def test_full_account_scan(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_account_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.display_name == "TestPlayer"
        assert result.account_id == "ACC-12345-67890"
        assert result.persona_id == "P-001"
        assert result.game_id == "G-002"
        assert result.email == "test@example.com"
        assert result.external_id == "EXT-003"
        assert result.country_code == "DE"
        assert result.access_token == "token-abc-def"

    def test_to_dict_excludes_access_token(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_account_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])
        d = result.to_dict()

        assert d["displayName"] == "TestPlayer"
        assert d["accountId"] == "ACC-12345-67890"
        assert "accessToken" not in d

    def test_missing_account_client_field(self):
        """If WrapperController has no AccountClient field, return empty with warning."""
        mem = MockMemory()
        # Build a WrapperController with only the rank field
        wc_name = 0x100000
        _write_ascii(mem, wc_name, "WrapperController")
        rank_field_name = 0x110000
        _write_ascii(mem, rank_field_name, "<PlayerRankServiceWrapper>k__BackingField")
        fields_ptr = 0x200000
        mem.write(fields_ptr, _make_field_info(name_ptr=rank_field_name, offset=0x18))
        wc_class = 0x300000
        mem.write(wc_class, _make_class(name_ptr=wc_name, fields_ptr=fields_ptr, field_count=1))
        wc_instance = 0x400000
        mem.write(wc_instance, _pack_ptr(wc_class))

        result = scan_account_from_instance(mem, wc_instance, wc_class)

        assert result.display_name == ""
        assert len(result.warnings) > 0
        assert "AccountClient" in result.warnings[0]

    def test_null_account_client_pointer(self):
        """If AccountClient pointer is null, return with warning."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)
        mem.write(addrs["wc_instance"] + 0x20, _pack_ptr(0))

        result = scan_account_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.display_name == ""
        assert len(result.warnings) > 0

    def test_null_account_info_pointer(self):
        """If AccountInformation pointer is null, return with warning."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)
        # Null out AccountInformation pointer
        ac_instance = 0x460000
        mem.write(ac_instance + 0x18, _pack_ptr(0))

        result = scan_account_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.display_name == ""
        assert len(result.warnings) > 0

    def test_partial_account_info(self):
        """If only some fields are present, others should be empty strings."""
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)
        # Null out email and external_id string pointers
        ai_instance = addrs["ai_instance"]
        # Email is field index 4, offset 0x10 + 4*8 = 0x30
        mem.write(ai_instance + 0x30, _pack_ptr(0))
        # ExternalID is field index 5, offset 0x10 + 5*8 = 0x38
        mem.write(ai_instance + 0x38, _pack_ptr(0))

        result = scan_account_from_instance(mem, addrs["wc_instance"], addrs["wc_class"])

        assert result.display_name == "TestPlayer"
        assert result.email == ""
        assert result.external_id == ""
        assert result.country_code == "DE"


# ---------------------------------------------------------------------------
# scan_ranks_and_account (full entry point) tests
# ---------------------------------------------------------------------------

class TestScanRanksAndAccount:
    """Tests for the full scan_ranks_and_account entry point."""

    def test_full_scan_with_pre_discovered_addresses(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_and_account(
            mem,
            wrapper_instance=addrs["wc_instance"],
            wrapper_class=addrs["wc_class"],
        )

        assert result.ranks.player_id == "TEST-PLAYER-123"
        assert result.ranks.constructed.class_name == "Platinum"
        assert result.ranks.limited.class_name == "Silver"
        assert result.account.display_name == "TestPlayer"
        assert result.account.country_code == "DE"
        assert result.wrapper_instance == addrs["wc_instance"]

    def test_ranks_only(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_and_account(
            mem,
            wrapper_instance=addrs["wc_instance"],
            wrapper_class=addrs["wc_class"],
            read_account=False,
        )

        assert result.ranks.player_id == "TEST-PLAYER-123"
        assert result.account.display_name == ""  # Should be empty

    def test_scan_ranks_convenience_function(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        ranks = scan_ranks(
            mem,
            wrapper_instance=addrs["wc_instance"],
            wrapper_class=addrs["wc_class"],
        )

        assert ranks.player_id == "TEST-PLAYER-123"
        assert ranks.constructed.class_name == "Platinum"

    def test_no_addresses_provided(self):
        """Without any addresses or data_segment_base, should return with warning."""
        mem = MockMemory()

        result = scan_ranks_and_account(mem)

        assert len(result.warnings) > 0
        assert result.ranks.player_id == ""

    def test_to_dict_structure(self):
        mem = MockMemory()
        addrs = _setup_wrapper_controller(mem)

        result = scan_ranks_and_account(
            mem,
            wrapper_instance=addrs["wc_instance"],
            wrapper_class=addrs["wc_class"],
        )
        d = result.to_dict()

        assert "ranks" in d
        assert "account" in d
        assert "warnings" in d
        assert d["ranks"]["playerId"] == "TEST-PLAYER-123"
        assert d["ranks"]["constructed"]["class"] == "Platinum"
        assert d["account"]["displayName"] == "TestPlayer"


# ---------------------------------------------------------------------------
# Formatting helpers tests
# ---------------------------------------------------------------------------

class TestFormatRankSummary:
    """Tests for format_rank_summary."""

    def test_platinum_with_record(self):
        ranks = RankScanResult(
            constructed=RankInfo(
                class_value=5,
                class_name="Platinum",
                level=4,
                step=2,
                wins=12,
                losses=5,
                draws=1,
                leaderboard_place=1274,
            ),
        )
        summary = format_rank_summary(ranks)
        assert "Platinum" in summary
        assert "Level 4" in summary
        assert "Step 2" in summary
        assert "#1274" in summary
        assert "12-5" in summary
        assert "1" in summary  # draws

    def test_unranked(self):
        ranks = RankScanResult(
            constructed=RankInfo(class_value=0, class_name="None"),
        )
        summary = format_rank_summary(ranks)
        assert "Kein Rang" in summary

    def test_no_record(self):
        ranks = RankScanResult(
            constructed=RankInfo(
                class_value=4,
                class_name="Gold",
                level=3,
                wins=0,
                losses=0,
                draws=0,
            ),
        )
        summary = format_rank_summary(ranks)
        assert "Gold" in summary
        assert "Level 3" in summary
        # No record part
        assert "0-0" not in summary


class TestFormatRankTable:
    """Tests for format_rank_table."""

    def test_full_table(self):
        ranks = RankScanResult(
            player_id="PLAYER-001",
            constructed=RankInfo(
                class_value=5,
                class_name="Platinum",
                level=4,
                step=2,
                wins=12,
                losses=5,
                draws=1,
                season_ordinal=45,
                percentile="0.75",
                leaderboard_place=1274,
            ),
            limited=RankInfo(
                class_value=3,
                class_name="Silver",
                level=1,
                wins=3,
                losses=2,
                draws=0,
                season_ordinal=45,
            ),
        )
        table = format_rank_table(ranks)
        assert "PLAYER-001" in table
        assert "Constructed" in table
        assert "Limited" in table
        assert "Platinum" in table
        assert "Silver" in table
        assert "12-5-1" in table
        assert "3-2-0" in table
        assert "0.75" in table
        assert "#1274" in table


class TestFormatAccountSummary:
    """Tests for format_account_summary."""

    def test_full_summary(self):
        account = AccountInfo(
            display_name="TestPlayer",
            country_code="DE",
            account_id="ACC-12345-67890",
        )
        summary = format_account_summary(account)
        assert "TestPlayer" in summary
        assert "[DE]" in summary
        assert "ACC-1234" in summary  # truncated to first 8 chars

    def test_empty_account(self):
        account = AccountInfo()
        summary = format_account_summary(account)
        assert "Keine Account-Info" in summary


class TestFormatAccountTable:
    """Tests for format_account_table."""

    def test_full_table(self):
        account = AccountInfo(
            display_name="TestPlayer",
            account_id="ACC-123",
            persona_id="P-001",
            game_id="G-002",
            country_code="DE",
            email="test@example.com",
            external_id="EXT-003",
        )
        table = format_account_table(account)
        assert "TestPlayer" in table
        assert "ACC-123" in table
        assert "P-001" in table
        assert "G-002" in table
        assert "DE" in table
        assert "test@example.com" in table
        assert "EXT-003" in table

    def test_empty_table(self):
        account = AccountInfo()
        table = format_account_table(account)
        assert "keine Daten" in table