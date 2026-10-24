import pytest

from remove_bgm import timecode
from remove_bgm.timecode import TimecodeError


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", None),
        ("   ", None),
        (None, None),
        ("83", 83.0),
        ("1:23", 83.0),
        ("01:23", 83.0),
        ("1:23.5", 83.5),
        ("0:00", 0.0),
        ("1:02:03", 3723.0),
        ("01:02:03.25", 3723.25),
        ("90:00", 5400.0),  # 第一段可以超過 59
        (" 1:23 ", 83.0),
    ],
)
def test_parse_valid(text, expected):
    assert timecode.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    ["abc", "1:2:3:4", "1:60", "1:61:00", "1::2", "-5", "1.5:20", "1:23,5", ":30", "1:"],
)
def test_parse_invalid(text):
    with pytest.raises(TimecodeError) as e:
        timecode.parse(text)
    assert "時間格式錯誤" in str(e.value)


def test_validate_range_defaults():
    assert timecode.validate_range(None, None, 100) == (0.0, None)
    assert timecode.validate_range(10, 20, 100) == (10, 20)


def test_validate_range_end_before_start():
    with pytest.raises(TimecodeError, match="結束時間必須晚於開始時間"):
        timecode.validate_range(20, 10, None)
    with pytest.raises(TimecodeError, match="結束時間必須晚於開始時間"):
        timecode.validate_range(20, 20, None)


def test_validate_range_beyond_duration():
    with pytest.raises(TimecodeError, match="開始時間超出影片長度"):
        timecode.validate_range(100, None, 60)
    with pytest.raises(TimecodeError, match="結束時間超出影片長度"):
        timecode.validate_range(0, 61, 60)
    # 些微超出（四捨五入誤差）視為到結尾
    assert timecode.validate_range(0, 60.03, 60) == (0, 60)


@pytest.mark.parametrize(
    "seconds,display,compact",
    [(0, "0:00", "0000"), (83, "1:23", "0123"), (83.5, "1:23.5", "0123"), (3723, "1:02:03", "010203")],
)
def test_format(seconds, display, compact):
    assert timecode.format_display(seconds) == display
    assert timecode.format_compact(seconds) == compact
