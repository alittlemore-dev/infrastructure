FROM nginxinc/nginx-unprivileged:1.31.3-alpine

USER root

RUN rm -f /etc/nginx/conf.d/default.conf

COPY --chown=nginx:nginx infra/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --chown=nginx:nginx infra/nginx/templates/ /etc/nginx/runtime-templates/
COPY --chown=nginx:nginx --chmod=755 infra/scripts/nginx_entrypoint.sh /usr/local/bin/alittlemore-nginx-entrypoint
COPY --chown=nginx:nginx --chmod=755 infra/scripts/nginx_healthcheck.sh /usr/local/bin/alittlemore-nginx-healthcheck

USER nginx
