#!/bin/sh
# Start n8n in background, patch UI after cache builds, then wait
/docker-entrypoint.sh &
N8N_PID=$!

# Wait for cache to be built
for i in $(seq 1 30); do
  if [ -f /home/node/.cache/n8n/public/index.html ]; then
    # Inject CSS to hide Chat beta
    cp /tmp/hide-chat.css /home/node/.cache/n8n/public/static/hide-chat.css 2>/dev/null
    sed -i 's|</head>|<link rel="stylesheet" href="/static/hide-chat.css"></head>|' /home/node/.cache/n8n/public/index.html
    break
  fi
  sleep 1
done

wait $N8N_PID
