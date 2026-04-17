name: Keep Workflows Active

# GitHub suspends scheduled workflows on repos with no activity in 60 days.
# This workflow commits a timestamp file daily to prevent that suspension.
# It runs at 6:50 AM EST (10 min before the scanner) to ensure the repo
# is "active" when the scanner fires.

on:
  schedule:
    - cron: '50 11 * * 1-5'  # 6:50 AM EST = 11:50 UTC
  workflow_dispatch:

jobs:
  keep-alive:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Touch activity file
        run: |
          echo "Last keep-alive: $(date -u '+%Y-%m-%d %H:%M UTC')" > .github/last_active.txt
          git config user.name  "Peregrine Scanner Bot"
          git config user.email "scanner@peregrine.io"
          git add .github/last_active.txt
          git diff --staged --quiet || git commit -m "chore: keep workflows active [skip ci]"
          git push
