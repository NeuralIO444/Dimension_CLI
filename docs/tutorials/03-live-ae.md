# Live After Effects

Headless math does not need After Effects. These two commands do.

```sh
dimension ae probe
dimension ae mask show --preset tiktok_video
dimension ae mask hide
```

`ae probe` sends no job. No poller is a result, exit 0, with a hint. A live command that cannot run exits 69.

This gate is not run in CI. It needs a Mac with After Effects and `Scripts/Dimension_Launcher.jsx` running.
