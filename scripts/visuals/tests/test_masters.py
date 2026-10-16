import io
import json

import boto3
import masters
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber


@pytest.fixture
def mdir(tmp_path):
    return tmp_path / "manifests"


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "work"
    monkeypatch.setenv("BRIDGE_VISUALS_WORK", str(root))
    return root


@pytest.fixture
def s3():
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def test_record_round_trip(mdir):
    key = "demo/plate/plate_a.png"
    masters.record(
        "demo", key, {"model": "m", "prompt": "room", "cost_usd": 0.113}, mdir
    )
    masters.record("demo", key, {"size": [4096, 3072]}, mdir)
    masters.record("demo", "demo/composites/level_5/c.png", {"cost_usd": 0.05}, mdir)
    masters.accept("demo", "plate", key, mdir)

    data = json.loads(masters.manifest_path("demo", mdir).read_text())
    entry = data["objects"][key]
    assert (
        entry["model"] == "m"
        and entry["size"] == [4096, 3072]
        and "recorded_at" in entry
    )
    assert data["accepted"] == {"plate": key}
    assert masters.accepted("demo", "plate", mdir) == key
    assert masters.spend(data) == {"total_usd": 0.163, "accepted_usd": 0.113}


def test_record_rejects_foreign_keys_and_accept_unknown(mdir):
    with pytest.raises(ValueError):
        masters.record("demo", "other/plate/x.png", {}, mdir)
    with pytest.raises(SystemExit):
        masters.accept("demo", "plate", "demo/plate/missing.png", mdir)


def test_accepted_slots_sort_numerically(mdir):
    for slot in ("level_10", "level_2", "plate"):
        key = f"demo/{slot}.png"
        masters.record("demo", key, {}, mdir)
        masters.accept("demo", slot, key, mdir)
    assert list(masters.load_manifest("demo", mdir)["accepted"]) == [
        "plate",
        "level_2",
        "level_10",
    ]


def test_ensure_bucket_locks_it_down(s3):
    client, stub = s3
    b = "bridge-visual-masters-123"
    stub.add_client_error(
        "create_bucket", "BucketAlreadyOwnedByYou", expected_params={"Bucket": b}
    )
    stub.add_response(
        "put_public_access_block",
        {},
        {
            "Bucket": b,
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        },
    )
    stub.add_response(
        "put_bucket_versioning",
        {},
        {"Bucket": b, "VersioningConfiguration": {"Status": "Enabled"}},
    )
    stub.add_response(
        "put_bucket_encryption",
        {},
        {
            "Bucket": b,
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
        },
    )
    masters.ensure_bucket(client, b)


def _write(root, key, data):
    p = root / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_push_uploads_only_new_or_changed_masters(s3, work):
    client, stub = s3
    b = "bkt"
    _write(work, "demo/layers/patient_0.png", b"new")
    same = _write(work, "demo/plate/p.png", b"same")
    _write(work, "demo/contact_sheet.html", b"not a master")
    # Walked in MASTER_DIRS order: plate/ before layers/.
    stub.add_response(
        "head_object",
        {"Metadata": {"sha256": masters.sha256_file(same)}},
        {"Bucket": b, "Key": "demo/plate/p.png"},
    )
    stub.add_client_error(
        "head_object",
        "404",
        http_status_code=404,
        expected_params={"Bucket": b, "Key": "demo/layers/patient_0.png"},
    )
    stub.add_response(
        "put_object",
        {},
        {
            "Bucket": b,
            "Key": "demo/layers/patient_0.png",
            "Body": b"new",
            "ContentType": "image/png",
            "Metadata": {"sha256": ANY},
        },
    )
    assert masters.push(client, b, "demo") == ["demo/layers/patient_0.png"]


def test_pull_fetches_missing_and_skips_present(s3, work, mdir):
    client, stub = s3
    b = "bkt"
    _write(work, "demo/plate/p.png", b"have")
    stub.add_response(
        "list_objects_v2",
        {
            "IsTruncated": True,
            "NextContinuationToken": "t",
            "Contents": [{"Key": "demo/plate/p.png", "Size": 4}],
        },
        {"Bucket": b, "Prefix": "demo/"},
    )
    stub.add_response(
        "list_objects_v2",
        {
            "IsTruncated": False,
            "Contents": [{"Key": "demo/layers/patient_3.png", "Size": 5}],
        },
        {"Bucket": b, "Prefix": "demo/", "ContinuationToken": "t"},
    )
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(b"bytes"), 5)},
        {"Bucket": b, "Key": "demo/layers/patient_3.png"},
    )
    assert masters.pull(client, b, "demo", manifest_dir=mdir) == [
        "demo/layers/patient_3.png"
    ]
    assert (work / "demo/layers/patient_3.png").read_bytes() == b"bytes"


def test_bucket_name_uses_the_account_id(monkeypatch):
    monkeypatch.delenv("BRIDGE_VISUALS_BUCKET", raising=False)
    sts = boto3.client(
        "sts", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    with Stubber(sts) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/u",
                "UserId": "u",
            },
        )
        assert masters.bucket_name(sts) == "bridge-visual-masters-123456789012"
