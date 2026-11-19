# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

import capa.features.address
from capa.engine import Or, Range
from capa.features.insn import Number
from capa.features.common import Regex, String, Substring
from capa.features.address import (
    ThreadAddress,
    ProcessAddress,
    DynamicCallAddress,
    DNTokenOffsetAddress,
    AbsoluteVirtualAddress,
    RelativeVirtualAddress,
)

ADDR1 = capa.features.address.AbsoluteVirtualAddress(0x401001)
ADDR2 = capa.features.address.AbsoluteVirtualAddress(0x401002)
ADDR3 = capa.features.address.AbsoluteVirtualAddress(0x401003)
ADDR4 = capa.features.address.AbsoluteVirtualAddress(0x401004)


def test_no_address_equality():
    no_addr = capa.features.address.NO_ADDRESS
    addr_zero = capa.features.address.AbsoluteVirtualAddress(0)

    assert no_addr == no_addr
    assert no_addr != addr_zero
    assert addr_zero != no_addr
    assert no_addr != ADDR1


def test_no_address_hash():
    no_addr = capa.features.address.NO_ADDRESS
    addr_zero = capa.features.address.AbsoluteVirtualAddress(0)

    assert hash(no_addr) != hash(addr_zero)

    s = {no_addr, addr_zero}
    assert len(s) == 2

    d = {no_addr: "no", addr_zero: "zero"}
    assert d[no_addr] == "no"
    assert d[addr_zero] == "zero"


def test_relative_address():
    with pytest.warns(DeprecationWarning):
        _ = RelativeVirtualAddress(0)


def test_dn_token_offset_address_cross_type_eq():
    addr = DNTokenOffsetAddress(0x1000, 0x10)
    assert (addr == AbsoluteVirtualAddress(0x1010)) is False
    assert (addr == "not an address") is False
    assert (addr == None) is False  # noqa: E711
    assert (addr == DNTokenOffsetAddress(0x1000, 0x10)) is True
    assert (addr == DNTokenOffsetAddress(0x1000, 0x11)) is False


def test_dn_token_offset_address_cross_type_lt():
    addr = DNTokenOffsetAddress(0x1000, 0x10)
    assert addr.__lt__(AbsoluteVirtualAddress(0x1010)) is NotImplemented
    assert addr.__lt__("not an address") is NotImplemented
    assert (addr < DNTokenOffsetAddress(0x1000, 0x11)) is True
    assert (addr < DNTokenOffsetAddress(0x1000, 0x10)) is False


def test_short_circuit():
    assert bool(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}})) is True

    # with short circuiting, only the children up until the first satisfied child are captured.
    assert len(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}}, short_circuit=True).children) == 1
    assert len(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}}, short_circuit=False).children) == 2


def test_eval_order():
    # base cases.
    assert bool(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}})) is True
    assert bool(Or([Number(1), Number(2)]).evaluate({Number(2): {ADDR1}})) is True

    # with short circuiting, only the children up until the first satisfied child are captured.
    assert len(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}}).children) == 1
    assert len(Or([Number(1), Number(2)]).evaluate({Number(2): {ADDR1}}).children) == 2
    assert len(Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}, Number(2): {ADDR1}}).children) == 1

    # and its guaranteed that children are evaluated in order.
    assert Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}}).children[0].statement == Number(1)
    assert Or([Number(1), Number(2)]).evaluate({Number(1): {ADDR1}}).children[0].statement != Number(2)

    assert Or([Number(1), Number(2)]).evaluate({Number(2): {ADDR1}}).children[1].statement == Number(2)
    assert Or([Number(1), Number(2)]).evaluate({Number(2): {ADDR1}}).children[1].statement != Number(1)


def test_address_cross_type_eq():
    proc = ProcessAddress(pid=1, ppid=0)
    ava = capa.features.address.AbsoluteVirtualAddress(0x401001)

    assert (proc == ava) is False
    assert (ava == proc) is False


def test_process_address_sorting():
    proc1 = ProcessAddress(pid=1, ppid=0)
    proc2 = ProcessAddress(pid=2, ppid=0)

    assert sorted([proc2, proc1]) == [proc1, proc2]


def test_process_address_cross_type_sort_raises():
    proc = ProcessAddress(pid=1, ppid=0)
    ava = capa.features.address.AbsoluteVirtualAddress(0x401001)

    with pytest.raises(TypeError):
        sorted([proc, ava])


def test_process_address_lt_returns_not_implemented_for_other_types():
    proc = ProcessAddress(pid=1, ppid=0)
    ava = capa.features.address.AbsoluteVirtualAddress(0x401001)

    assert proc.__lt__(ava) is NotImplemented


def test_thread_address_cross_type_eq():
    proc = ProcessAddress(pid=1, ppid=0)
    thread = ThreadAddress(process=proc, tid=10)
    ava = capa.features.address.AbsoluteVirtualAddress(0x401001)

    assert (thread == ava) is False
    assert (ava == thread) is False


def test_dynamic_call_address_cross_type_eq():
    proc = ProcessAddress(pid=1, ppid=0)
    thread = ThreadAddress(process=proc, tid=10)
    call = DynamicCallAddress(thread=thread, id=0)
    ava = capa.features.address.AbsoluteVirtualAddress(0x401001)

    assert (call == ava) is False
    assert (ava == call) is False


# substrings and regexes are never keys in the feature set,
# they match by scanning the String values that are.
# so `count(substring(...))` and `count(string(/.../))` have to evaluate the feature
# rather than look it up, and count the locations at which it matched.


def test_range_counts_substring_locations():
    # the substring is spread across multiple string values and multiple locations,
    # and the count is the number of distinct locations, not the number of values.
    features = {
        String("http://evil.example/a"): {ADDR1, ADDR2},
        String("see evil.example/b"): {ADDR3},
        String("nothing to see here"): {ADDR4},
    }

    assert bool(Range(Substring("evil.example/"), min=3, max=3).evaluate(features)) is True
    assert bool(Range(Substring("evil.example/"), min=3, max=None).evaluate(features)) is True
    assert bool(Range(Substring("evil.example/"), min=4, max=None).evaluate(features)) is False
    assert bool(Range(Substring("evil.example/"), min=None, max=2).evaluate(features)) is False
    assert bool(Range(Substring("evil.example/"), min=0, max=2).evaluate(features)) is False

    # the locations of the range are the locations of the matching strings.
    result = Range(Substring("evil.example/"), min=1, max=None).evaluate(features)
    assert result.locations == {ADDR1, ADDR2, ADDR3}


def test_range_counts_regex_locations():
    features = {
        String("http://evil.example/a"): {ADDR1, ADDR2},
        String("see evil.example/b"): {ADDR3},
        String("nothing to see here"): {ADDR4},
    }

    assert bool(Range(Regex(r"/evil\.example\/[a-z]+/"), min=3, max=3).evaluate(features)) is True
    assert bool(Range(Regex(r"/evil\.example\/[a-z]+/"), min=4, max=None).evaluate(features)) is False

    result = Range(Regex(r"/evil\.example\/[a-z]+/"), min=1, max=None).evaluate(features)
    assert result.locations == {ADDR1, ADDR2, ADDR3}


def test_range_counts_exact_string_for_contrast():
    # exact string features are hash lookup features: they are counted
    # by the locations recorded for the exact feature, and only that feature.
    features = {
        String("http://evil.example/a"): {ADDR1, ADDR2},
        String("see evil.example/b"): {ADDR3},
    }

    assert bool(Range(String("http://evil.example/a"), min=2, max=2).evaluate(features)) is True
    # the same value is present at two locations, and there's no third one:
    assert bool(Range(String("http://evil.example/a"), min=3, max=None).evaluate(features)) is False
    # `evil.example/` occurs at three locations, but the exact string occurs at two.
    assert bool(Range(String("http://evil.example/a"), min=1, max=2).evaluate(features)) is True


def test_range_min_zero_short_circuit():
    features = {String("hello world"): {ADDR1}}

    # when the feature is absent and min is zero, the range matches without evaluating
    # (and without collecting locations), whatever the max is.
    result = Range(Substring("absent"), min=0, max=5).evaluate(features)
    assert bool(result) is True
    assert result.locations == frozenset()

    result = Range(Regex(r"/absent/"), min=0, max=0).evaluate(features)
    assert bool(result) is True
    assert result.locations == frozenset()

    # the same behaviour for hash lookup features is preserved.
    result = Range(Number(0x999), min=0, max=0).evaluate(features)
    assert bool(result) is True
    assert result.locations == frozenset()

    # but when the feature is present, the max still bounds the count.
    assert bool(Range(Substring("hello"), min=0, max=0).evaluate(features)) is False
    assert bool(Range(Substring("hello"), min=0, max=1).evaluate(features)) is True
