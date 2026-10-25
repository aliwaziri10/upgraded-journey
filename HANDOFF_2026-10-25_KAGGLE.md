# HANDOFF 2026-10-25: Kaggle video fallback for Tiny Lawn Tales

NOT BUILT YET. Re-verify everything live. Longer notes also in `aliwaziri10/marius-command-center`, file `HANDOFF_2026-10-25_TLT_KAGGLE.md`.

- Decision (Zia, 2026-10-25): use free Kaggle GPU video generation (LTX-Video, as Marius's `scripts/kaggle_video_test/` does) because Agnes answers `video_queue_full`.
- Videos are stored in Supabase Storage: final in bucket `videos`, clips in `video_clips` (see `assembly/store.py`). Not B2.
- Sound (music bed plus ffmpeg effects from each shot's `sound` note) is added only in `assembly/assemble.py`, which runs after every shot has a clip. Never run on real clips yet. Raw Agnes clips are silent on purpose.
- Secret: a Kaggle API token was pasted in chat on 2026-10-25. Regenerate it after the first successful render.
- Access: `aliwaziri10` can write to the fork `aliwaziri10/upgraded-journey`, not to `adrian001234/upgraded-journey` (403 on 2026-10-25).
- Next: write the Kaggle runner and kernel, push, run a 4-shot test, judge the clips.
