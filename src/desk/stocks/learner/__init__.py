"""The Learner (docs/STOCKS_LEARNER.md): an experience log of every option trade KESTREL looked at, its point-in-time
features and what then happened, and an ordinary statistical model trained on it walk-forward and shown in shadow.

Research and PAPER only. Nothing here places an order, changes a Plays ranking, a PAPER strategy, the Learning book or
any calibration, and no language model judges a trade: the model is gradient-boosted trees (scikit-learn).

* ``actions``    the action space: a trade described by dials (structure, |delta|, days to expiry, width) and an
                 exit-rule grid, with a version hash;
* ``features``   every feature with its source and when it becomes known; readers that cannot see later data;
* ``experience`` the candidates recorded at each closing build (the grid, the Plays picks, PAPER and Learning entries
                 of the day) and the exploration slice;
* ``labels``     the daily, idempotent labelling job: value at 1/5/10/21 sessions and at expiry, excursions and every
                 exit rule's return on the same path, from stored closing quotes only;
* ``replay``     the same candidates and labels generated from the real-price replay downloads (source "replay");
* ``model``      walk-forward training, held-out evaluation, the append-only model registry and shadow predictions;
* ``view``       the shadow views (Plays, Learning report).
"""
