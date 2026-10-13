"""Refuse live input unless disposable fixtures are visible, separate and on the chosen display."""

from computer_artist.windows_monitors import contains


def verify_fixture_placement(monitor, targets):
    rects = [[w[key] for key in ('x', 'y', 'width', 'height')] for w in targets]
    problems = [
        f'{w["title"]} is outside the selected monitor'
        for w, rect in zip(targets, rects)
        if not contains(monitor, rect)
    ]
    for index, (x, y, width, height) in enumerate(rects):
        # 480x480 outer geometry leaves roughly 460x440 client pixels at 100% scaling.
        if not (300 <= width <= 700 and 300 <= height <= 700):
            problems.append(
                f'{targets[index]["title"]} has unexpected client size {width}x{height}'
            )
        for other_x, other_y, other_width, other_height in rects[index + 1 :]:
            if (
                x < other_x + other_width
                and other_x < x + width
                and y < other_y + other_height
                and other_y < y + height
            ):
                problems.append('Disposable fixtures overlap; input could reach the wrong one')
    if problems:
        raise ValueError(f'Stopping before input: {"; ".join(problems)}: {targets}')
