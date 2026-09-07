#!/usr/bin/env bash
set -euo pipefail

timeout 10m rsync \
    -avzr --delete \
    --exclude '.git' \
    --exclude '.deploy-state' \
    --exclude '.alittlemore-infra-deploy-root' \
    --exclude 'infra/nginx/certs/' \
    -e "ssh -i $HOME/.ssh/alittlemore-infra -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$HOME/.ssh/known_hosts" \
    .deploy-payload/ \
    "$VALIDATED_REMOTE_USER@$VALIDATED_REMOTE_HOST:$VALIDATED_REMOTE_PATH/.deploy-state/$VALIDATED_DEPLOY_STAGE/"
