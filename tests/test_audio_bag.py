import random
import threading
from collections import Counter

import main


def test_empty_bag_is_falsy():
    assert not main._AudioBag([])


def test_single_audio_always_returned():
    bag = main._AudioBag(["a.mp3"])
    assert [bag.next() for _ in range(5)] == ["a.mp3"] * 5


def test_each_round_uses_every_audio_once_without_back_to_back_repeat():
    paths = ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]
    bag = main._AudioBag(paths, rng=random.Random(1))
    picks = [bag.next() for _ in range(len(paths) * 50)]

    for i in range(0, len(picks), len(paths)):
        assert sorted(picks[i:i + len(paths)]) == sorted(paths)
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_thread_safe_even_distribution():
    paths = ["a.mp3", "b.mp3", "c.mp3"]
    bag = main._AudioBag(paths)
    picks = []
    lock = threading.Lock()

    def worker():
        for _ in range(100):
            p = bag.next()
            with lock:
                picks.append(p)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert Counter(picks) == {p: 200 for p in paths}
