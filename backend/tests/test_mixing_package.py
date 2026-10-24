import zipfile

import numpy as np
import pytest

from remove_bgm import mixing, package


@pytest.mark.parametrize("db,gain", [(0, 1.0), (6, 1.9953), (-6, 0.5012), (-12, 0.2512)])
def test_db_to_gain(db, gain):
    assert mixing.db_to_gain(db) == pytest.approx(gain, abs=1e-4)


def test_mix_applies_gains():
    d = np.full((2, 100), 0.1, dtype=np.float32)
    e = np.full((2, 100), 0.1, dtype=np.float32)
    assert mixing.mix(d, e)[0, 0] == pytest.approx(0.2)
    assert mixing.mix(d, e, 6, -12)[0, 0] == pytest.approx(0.1 * 1.9953 + 0.1 * 0.2512, abs=1e-4)
    assert mixing.mix(d, e, -12, 0)[0, 0] == pytest.approx(0.1 * 0.2512 + 0.1, abs=1e-4)


def test_mix_prevents_clipping():
    d = np.full((2, 10), 0.8, dtype=np.float32)
    out = mixing.mix(d, d, 6, 6)
    assert np.abs(out).max() == pytest.approx(0.999)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("鬼滅之刃 / 無限列車: 預告", "鬼滅之刃 _ 無限列車_ 預告"),
        ('a<b>c"d|e?f*', "a_b_c_d_e_f"),
        ("...", "video"),
        ("", "video"),
        ("x" * 100, "x" * 60),
    ],
)
def test_safe_name(title, expected):
    assert package.safe_name(title) == expected


def test_folder_name():
    assert package.folder_name("Movie", 83, 225) == "Movie_0123-0345"


def test_unique_dir(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a (2).zip").write_text("")
    assert package.unique_dir(tmp_path, "a").name == "a (3)"
    assert package.unique_dir(tmp_path, "b").name == "b"


def test_make_zip(tmp_path):
    folder = tmp_path / "Movie_0000-0010"
    folder.mkdir()
    (folder / "dialogue.wav").write_bytes(b"1")
    (folder / "effects.wav").write_bytes(b"2")
    z = package.make_zip(folder)
    assert z == tmp_path / "Movie_0000-0010.zip"
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == ["Movie_0000-0010/dialogue.wav", "Movie_0000-0010/effects.wav"]
