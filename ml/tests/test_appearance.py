import numpy as np

from pickleball_ml.players.appearance import (
    DESCRIPTOR_SIZE,
    appearance_distance,
    mean_descriptor,
    torso_histogram,
)


def person(shirt_bgr: tuple[int, int, int]) -> np.ndarray:
    image = np.zeros((200, 100, 3), dtype=np.uint8)
    image[40:110, 25:75] = shirt_bgr  # torso region of a box covering the whole image
    return image


def test_same_shirt_is_close_and_different_shirts_are_far() -> None:
    red = torso_histogram(person((0, 0, 220)), 0, 0, 100, 200)
    red_again = torso_histogram(person((10, 10, 210)), 0, 0, 100, 200)
    blue = torso_histogram(person((220, 60, 0)), 0, 0, 100, 200)
    assert red is not None and red.shape == (DESCRIPTOR_SIZE,)
    assert appearance_distance(red, red_again) < 0.1
    assert appearance_distance(red, blue) > 0.9


def test_degenerate_box_has_no_descriptor() -> None:
    assert torso_histogram(person((0, 0, 220)), 10, 10, 12, 12) is None


def test_missing_descriptor_is_uninformative() -> None:
    assert appearance_distance(None, None) == 0.5


def test_mean_descriptor_is_normalized() -> None:
    a = np.zeros(DESCRIPTOR_SIZE, dtype=np.float32)
    a[0] = 1
    b = np.zeros(DESCRIPTOR_SIZE, dtype=np.float32)
    b[1] = 1
    mean = mean_descriptor([a, b])
    assert mean is not None
    assert mean.sum() == np.float32(1.0)
    assert mean_descriptor([]) is None
