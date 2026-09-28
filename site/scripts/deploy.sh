#!/usr/bin/env bash
# Publishes the built site (site/dist) to https://evan.mx/fleetkit/. The one deploy procedure:
# .github/workflows/site.yml runs it on every push to main that changes the site, and it runs
# the same way by hand.
#
#   SITE_BUCKET=... SITE_DISTRIBUTION_ID=... site/scripts/deploy.sh
#
# Both values are in infra/site (`terraform output -raw site_bucket`, `... distribution_id`)
# and in the `site` environment secrets of the same names. Credentials come from the
# environment: the deploy role in CI, the default profile by hand.
#
# What a deploy owns under fleetkit/: index.html, assets/ and data/. Nothing else there is ever
# written, listed or deleted, so files put under fleetkit/ some other way, in a folder of their
# own, survive every deploy. index.html is replaced every time, so nothing may be edited into
# it after the build.
#
# Order matters. Hashed assets go first, then data, then index.html last, so no index.html is
# ever live before the files it references. Assets are never deleted: their names are content
# hashes, and a tab opened before a deploy may still load an old chunk. Objects under
# fleetkit/data/ that the build no longer has are deleted. Then CloudFront is invalidated and
# the script waits for it.
#
# Output is quiet (--only-show-errors, no bucket names) because the CI log is public.
set -euo pipefail

: "${SITE_BUCKET:?is not set: the evan.mx site bucket (infra/site: terraform output -raw site_bucket)}"
: "${SITE_DISTRIBUTION_ID:?is not set: the evan.mx distribution (infra/site: terraform output -raw distribution_id)}"

# The site's prefix. infra/site grants the deploy role index.html, assets/ and data/ under it.
PREFIX=fleetkit
DIST=${SITE_DIST:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/dist"}
DEST="s3://$SITE_BUCKET/$PREFIX"
IMMUTABLE='public,max-age=31536000,immutable'
SHORT='public,max-age=300'
export AWS_PAGER=''

for need in index.html assets data; do
  [ -e "$DIST/$need" ] || { echo "deploy: $DIST/$need is missing; run npm run build in site/ first" >&2; exit 1; }
done
# Anything else at the top of dist (a file Vite copied from public/) would need its own grant.
extra=$(cd "$DIST" && find . -mindepth 1 -maxdepth 1 ! -name index.html ! -name assets ! -name data ! -name .DS_Store)
if [ -n "$extra" ]; then
  echo "deploy: dist has top-level entries this script does not publish:" $extra >&2
  echo "deploy: add them here and to the deploy role's policy in infra/site/main.tf" >&2
  exit 1
fi

# put <source dir> <destination> <cache-control> [more `aws s3 cp` arguments]
# Later filters win, so the .DS_Store exclusion (a Finder file in a local dist) comes last.
put() {
  local src=$1 dest=$2 cache=$3
  shift 3
  aws s3 cp "$src" "$dest" --recursive --only-show-errors --cache-control "$cache" "$@" --exclude '*.DS_Store'
}

echo "deploy: assets"
put "$DIST/assets" "$DEST/assets" "$IMMUTABLE" --exclude '*' --include '*.js' --content-type 'text/javascript'
put "$DIST/assets" "$DEST/assets" "$IMMUTABLE" --exclude '*' --include '*.css' --content-type 'text/css'
put "$DIST/assets" "$DEST/assets" "$IMMUTABLE" --exclude '*.js' --exclude '*.css'

echo "deploy: data"
# JSON is revalidated on every load (no-cache): index.html is, so a returning visitor must never
# pair the new bundle with JSON from their browser cache. Images are named by their hash.
put "$DIST/data" "$DEST/data" 'no-cache' --exclude '*' --include '*.json' --content-type 'application/json'
# The CLI does not reliably know .webp, so the type is set explicitly.
put "$DIST/data" "$DEST/data" "$SHORT" --exclude '*' --include '*.webp' --content-type 'image/webp'
put "$DIST/data" "$DEST/data" "$SHORT" --exclude '*.json' --exclude '*.webp'

echo "deploy: index.html"
aws s3 cp "$DIST/index.html" "$DEST/index.html" --only-show-errors \
  --cache-control 'no-cache' --content-type 'text/html; charset=utf-8'

# Delete the data the new build no longer has: the keys under fleetkit/data/ minus dist/data.
wanted=$(cd "$DIST" && find data -type f ! -name .DS_Store | sed "s|^|$PREFIX/|" | LC_ALL=C sort)
[ -n "$wanted" ] || { echo "deploy: $DIST/data is empty; refusing to delete anything" >&2; exit 1; }
present=$(aws s3api list-objects-v2 --bucket "$SITE_BUCKET" --prefix "$PREFIX/data/" \
  --query 'Contents[].[Key]' --output text | awk -v p="$PREFIX/data/" 'index($0, p) == 1' | LC_ALL=C sort)
stale=$(LC_ALL=C comm -13 <(printf '%s\n' "$wanted") <(printf '%s\n' "$present"))
removed=0
while IFS= read -r key; do
  case $key in
    '' | */ | *..*) continue ;;   # folder markers and anything odd are left alone
    "$PREFIX"/data/*) ;;
    *) continue ;;
  esac
  echo "deploy: removing $key"
  aws s3 rm "s3://$SITE_BUCKET/$key" --only-show-errors
  removed=$((removed + 1))
done <<< "$stale"
echo "deploy: removed $removed stale data object(s)"

echo "deploy: invalidating /$PREFIX and /$PREFIX/*"
id=$(aws cloudfront create-invalidation --distribution-id "$SITE_DISTRIBUTION_ID" \
  --paths "/$PREFIX" "/$PREFIX/*" --query 'Invalidation.Id' --output text)
aws cloudfront wait invalidation-completed --distribution-id "$SITE_DISTRIBUTION_ID" --id "$id"
echo "deploy: done, https://evan.mx/$PREFIX/"
