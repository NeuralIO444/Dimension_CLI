# No After Effects

This is the check that the engine is installed. It does not open After Effects.

```sh
pip install dimension-cli
dimension
dimension safe-zone plan --preset tiktok_video
dimension --json safe-zone plan --preset tiktok_video
```

Success is a sentence naming the preset, then one JSON document with `"status": "OK"`. Logs stay on stderr.

`dimension man` is the local help. Topics: overview, commands, errors, mograph.
