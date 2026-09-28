"""The scrub turns identifiers into aliases; the gate refuses anything that slips through."""
import io
import json

import pytest
from PIL import Image

from images import encode
from scrub import Scrubber, gate, webp_chunks

# Planted identifiers are built from parts, so the repository's own leak check passes this file.
ACCOUNT = "123456" "789012"
ARN = "arn:" f"aws:iam::{ACCOUNT}:role/fleetkit-worker"
INSTANCE = "i-0123456789abcdef0"


def test_each_kind_becomes_a_short_alias():
    s = Scrubber()
    assert s.text(f"account {ACCOUNT}") == "account account-1"
    assert s.text(ARN) == "arn-1"                       # the whole ARN, not just its account id
    assert s.text(f"on {INSTANCE} from ami-0abcdef1234567890") == "on instance-1 from image-1"
    assert s.text("worker at 10.0.1.23, support at 172.31.0.9") == "worker at ip-1, support at ip-2"
    assert s.text("mail someone" "@example.com") == "mail email-1"


def test_aliases_are_stable():
    s = Scrubber()
    assert s.text(INSTANCE) == s.text(f"{INSTANCE}") == "instance-1"
    assert s.text("i-0fedcba9876543210") == "instance-2"
    # a second build over the same inputs in the same order gives the same aliases
    t = Scrubber()
    assert [t.text(x) for x in (INSTANCE, "i-0fedcba9876543210")] == ["instance-1", "instance-2"]


def test_values_that_only_look_similar_are_left_alone():
    s = Scrubber()
    for keep in ("154.0.8037.57-1~deb12u1", "6.12.46-66.121.amzn2023.x86_64", "v1.17.0", "10.5", "1790528960",
                 "Intel(R) Xeon(R) 6975P-C", "d12-t1", "m8i.4xlarge", "652f26d88cda86a453e31e29f9def2e771dd4971"):
        assert s.text(keep) == keep, keep
    assert s.counts == {}


def test_every_string_and_key_in_a_document():
    s = Scrubber()
    doc = {"host": {"id": INSTANCE, "notes": [f"owner {ACCOUNT}", 3, None, True]}, INSTANCE: 1}
    assert s.value(doc) == {"host": {"id": "instance-1", "notes": ["owner account-1", 3, None, True]}, "instance-1": 1}


def write(tmp_path, name, data):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


def webp(**save) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "WEBP", **save)
    return buf.getvalue()


def test_gate_passes_a_clean_dataset(tmp_path):
    write(tmp_path, "index.json", json.dumps({"cpu": 10.6, "version": "154.0.8037.57", "t": [1.5, 2.25]}))
    write(tmp_path, "img/abcdef12.t.webp", encode(Image.new("RGB", (640, 360), (0, 90, 200)), 320, 70, 10240))
    assert gate(tmp_path) == []


@pytest.mark.parametrize("planted, label", [
    (INSTANCE, "instance or resource id"),
    ("10.0.0.1", "IPv4 address"),
    (ACCOUNT, "12-digit number"),
    (ARN, "ARN"),
    ("someone" "@example.com", "email address"),
    ("ec2.us-east-1.amazonaws.com", "AWS hostname"),
    ("ip-10-0-1-23.ec2.internal", "AWS hostname"),
    ("s3://bucket/key", "S3 URL"),
    ("https://example.org/", "URL"),
    ("us-east-1a", "availability zone"),
])
def test_gate_fails_on_planted_identifiers(tmp_path, planted, label):
    write(tmp_path, "campaigns/x/campaign.json", json.dumps({"note": planted}))
    problems = gate(tmp_path)
    assert any(label in p for p in problems), problems


def test_gate_fails_on_image_metadata(tmp_path):
    exif = Image.Exif()
    exif[0x010E] = "planted"
    write(tmp_path, "img/00000001.f.webp", webp(exif=exif.tobytes()))
    write(tmp_path, "img/00000002.f.webp", webp(icc_profile=b"\0" * 128))
    problems = gate(tmp_path)
    assert any("00000001" in p and "EXIF" in p for p in problems), problems
    assert any("00000002" in p and "ICCP" in p for p in problems), problems


def test_gate_refuses_other_file_types(tmp_path):
    write(tmp_path, "img/a.jpg", b"\xff\xd8")
    assert any("only JSON and WebP" in p for p in gate(tmp_path))


def test_gate_allow_needs_the_exact_text_and_a_reason(tmp_path):
    write(tmp_path, "index.json", json.dumps({"n": ACCOUNT}))
    assert gate(tmp_path, [{"text": ACCOUNT, "reason": "a product count, not an account"}]) == []
    assert gate(tmp_path, [{"text": ACCOUNT}]) != []


def test_encoded_images_carry_no_metadata():
    src = Image.new("RGB", (1280, 713), (10, 120, 60))
    src.info["icc_profile"] = b"\0" * 128
    for data in (encode(src, 320, 70, 10240), encode(src, 1280, 75, 61440)):
        assert set(webp_chunks(data)) <= {b"VP8 ", b"VP8L", b"VP8X"}
    with Image.open(io.BytesIO(encode(src, 320, 70, 10240))) as im:
        assert im.size == (320, 178)
    with Image.open(io.BytesIO(encode(Image.new("RGB", (200, 100)), 320, 70, 10240))) as im:
        assert im.size == (200, 100)        # never upscaled
