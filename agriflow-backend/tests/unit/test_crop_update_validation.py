"""
Unit tests for CropUpdateIn and CropDeleteIn validation rules.
"""
import pytest
from pydantic import ValidationError

from app.models.enums import CropStage
from app.schemas.farmer_app import CropDeleteIn, CropUpdateIn


def test_crop_update_stage_and_comment():
    data = CropUpdateIn(status="Growing", farmer_comment="the crop has been sowed well")
    assert data.status == CropStage.GROWING
    assert data.farmer_comment == "the crop has been sowed well"


def test_crop_update_acres_and_comment():
    data = CropUpdateIn(acres=10.5, farmer_comment="Expanded field acreage")
    assert data.acres == 10.5
    assert data.farmer_comment == "Expanded field acreage"


def test_crop_update_stage_only_no_comment():
    data = CropUpdateIn(status="Harvest")
    assert data.status == CropStage.HARVEST
    assert data.farmer_comment is None


def test_crop_update_acres_only_no_comment():
    data = CropUpdateIn(acres=15.0)
    assert data.acres == 15.0
    assert data.farmer_comment is None


def test_crop_update_whitespace_only_comment_fails():
    with pytest.raises(ValidationError) as exc_info:
        CropUpdateIn(farmer_comment="    ")
    assert "Farmer comment cannot be empty or whitespace only" in str(exc_info.value)


def test_crop_update_comment_exceeds_1000_chars_fails():
    long_comment = "a" * 1001
    with pytest.raises(ValidationError):
        CropUpdateIn(farmer_comment=long_comment)


def test_crop_delete_reason_valid():
    data = CropDeleteIn(reason="Crop damaged due to heavy rainfall")
    assert data.reason == "Crop damaged due to heavy rainfall"


def test_crop_delete_reason_short_or_whitespace_fails():
    with pytest.raises(ValidationError):
        CropDeleteIn(reason="  a  ")
