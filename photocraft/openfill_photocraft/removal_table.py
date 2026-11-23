# SPDX-License-Identifier: Apache-2.0
"""Which removal methods Automatic tries, by how much of the picture is selected.

Derived from an artist's blind ratings of the same removals made with every method. The order is
data from that evaluation, so re-measure before changing it by hand.
"""

MEASURED = "2026-11-23"
PICTURES = 54

# (selection share of the picture below this, ((method family, how many of its variations to show), ...))
SIZES = (
    (0.01, (("rorem", 2), ("qwen", 1), ("lama", 1))),
    (0.05, (("qwen", 1), ("rorem", 2), ("lama", 1))),
    (1.01, (("rorem", 2), ("qwen", 1), ("lama", 1))),
)
