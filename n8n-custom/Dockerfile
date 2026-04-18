# Custom n8n image that bakes a CSS override into the editor-ui
# to hide Chat / AI Assistant / AI Agent entry points. LazyClaw is
# the AI layer — n8n here is workflow-only.

FROM n8nio/n8n:latest

USER root

COPY hide-chat.css /tmp/hide-chat.css

# The path is long because n8n uses pnpm workspaces inside the image.
# If n8n ever restructures, `docker exec` + `find` will locate the
# dist dir again; update the path here accordingly.
RUN set -eu; \
    UI_DIR="/usr/local/lib/node_modules/n8n/node_modules/.pnpm/n8n-editor-ui@file+packages+frontend+editor-ui/node_modules/n8n-editor-ui/dist"; \
    if [ ! -d "$UI_DIR" ]; then \
      echo "editor-ui dist not found at $UI_DIR — locate + update Dockerfile"; \
      find /usr/local/lib/node_modules/n8n -path '*editor-ui*/dist/index.html' -print | head -5; \
      exit 1; \
    fi; \
    mkdir -p "$UI_DIR/static"; \
    cp /tmp/hide-chat.css "$UI_DIR/static/hide-chat.css"; \
    sed -i 's|</head>|<link rel="stylesheet" href="/static/hide-chat.css"></head>|' "$UI_DIR/index.html"; \
    rm /tmp/hide-chat.css

USER node
