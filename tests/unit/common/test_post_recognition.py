from __future__ import annotations

from common.post_recognition import get_post_recognition_milestones


def test_get_post_recognition_milestones_crosses_single_milestone():
    assert get_post_recognition_milestones(9, 10, [10]) == [10]


def test_get_post_recognition_milestones_does_not_retrigger_after_milestone():
    assert get_post_recognition_milestones(10, 11, [10]) == []


def test_get_post_recognition_milestones_from_zero():
    assert get_post_recognition_milestones(0, 10, [10]) == [10]


def test_get_post_recognition_milestones_handles_jump():
    assert get_post_recognition_milestones(9, 12, [10]) == [10]


def test_get_post_recognition_milestones_no_change():
    assert get_post_recognition_milestones(10, 10, [10]) == []


def test_get_post_recognition_milestones_multiple_milestones():
    milestones = [10, 20, 50]

    assert get_post_recognition_milestones(9, 55, milestones) == [10, 20, 50]
    assert get_post_recognition_milestones(9, 12, milestones) == [10]
    assert get_post_recognition_milestones(19, 25, milestones) == [20]
    assert get_post_recognition_milestones(49, 55, milestones) == [50]


def test_select_post_recognition_milestone_for_notification():
    from common.post_recognition import select_post_recognition_milestone_for_notification

    assert select_post_recognition_milestone_for_notification([10, 20, 50]) == 50
    assert select_post_recognition_milestone_for_notification([10]) == 10
    assert select_post_recognition_milestone_for_notification([]) is None
