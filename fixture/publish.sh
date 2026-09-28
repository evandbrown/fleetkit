#!/usr/bin/env bash
# Publishes a public copy of the fixture to https://evan.mx/fleetkit-fixture/ so that reviewers
# can see the site the task runs against. By hand, with the default profile: the CI deploy role
# can write under fleetkit/ only, and the copy changes only when the fixture does.
#
#   SITE_BUCKET=... SITE_DISTRIBUTION_ID=... fixture/publish.sh    # build, re-root, upload, invalidate
#   fixture/publish.sh --check                                      # build and re-root only, no AWS
#
# Both values are in infra/site (`terraform output -raw site_bucket`, `... distribution_id`).
# The evan.mx router (a CloudFront Function outside this repository, Evan's) must list the
# prefix, or every request under it is answered 404 before it reaches the bucket.
#
# What it does: a fresh build (build.py, the default seed) into a temporary directory; a copy
# with every URL re-rooted under the prefix (reroot.py, which refuses a copy with a dangling
# reference); an upload of that copy with content types set by extension and a short cache
# life, deleting whatever under the prefix the build no longer has; then an invalidation of
# the prefix, waited for. Nothing outside the prefix is touched.
#
# The copy is for looking at, not for measuring: CloudFront compresses and caches it, so its
# bytes differ from manifest.json, which describes the root-served build nginx gives the guests.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREFIX=${FIXTURE_PREFIX:-fleetkit-fixture}
SHORT='public,max-age=300'
check_only=false
[ "${1:-}" = "--check" ] && check_only=true
export AWS_PAGER=''

if ! $check_only; then
  : "${SITE_BUCKET:?is not set: the evan.mx site bucket (infra/site: terraform output -raw site_bucket)}"
  : "${SITE_DISTRIBUTION_ID:?is not set: the evan.mx distribution (infra/site: terraform output -raw distribution_id)}"
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/fixture-publish.XXXXXX")
trap 'rm -rf "$work"' EXIT

echo "publish: building"
python3 "$HERE/build.py" --out "$work/dist" --quiet --digest
echo "publish: re-rooting under /$PREFIX"
python3 "$HERE/reroot.py" --src "$work/dist" --out "$work/public" --prefix "/$PREFIX"
if $check_only; then
  echo "publish: check only; $(find "$work/public" -type f | wc -l | tr -d ' ') files ready, nothing uploaded"
  exit 0
fi

DEST="s3://$SITE_BUCKET/$PREFIX"
# put <extension> <content type>: one sync per type, so each object carries its type.
put() {
  aws s3 sync "$work/public" "$DEST" --only-show-errors --cache-control "$SHORT" \
    --exclude '*' --include "*.$1" --content-type "$2"
}
echo "publish: uploading"
put html 'text/html; charset=utf-8'
put css  'text/css'
put js   'text/javascript'
put json 'application/json'
put png  'image/png'
put svg  'image/svg+xml'
# Anything of another type, and the deletion of what the build no longer has (under the
# prefix only; sync never looks outside it).
aws s3 sync "$work/public" "$DEST" --only-show-errors --cache-control "$SHORT" --delete --exclude '*.DS_Store'

echo "publish: invalidating /$PREFIX and /$PREFIX/*"
id=$(aws cloudfront create-invalidation --distribution-id "$SITE_DISTRIBUTION_ID" \
  --paths "/$PREFIX" "/$PREFIX/*" --query 'Invalidation.Id' --output text)
aws cloudfront wait invalidation-completed --distribution-id "$SITE_DISTRIBUTION_ID" --id "$id"
echo "publish: done, https://evan.mx/$PREFIX/"
