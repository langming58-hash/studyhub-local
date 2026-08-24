# Install Help

Thanks for trying it. The fastest way to debug is usually:

```bash
python3 --version
npm --version
npm run ci
npm run dev
```

If port `8765` is already in use, try:

```bash
python3 server.py serve --port 8876
```

Then install locally and open:

```text
http://127.0.0.1:8876
```

If you can share the exact error output with private paths redacted, I can help narrow it down.
