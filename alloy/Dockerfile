FROM grafana/alloy:latest
COPY config.alloy /etc/alloy/config.alloy
CMD ["run", "--server.http.listen-addr=0.0.0.0:12345", "--storage.path=/var/lib/alloy/data", "/etc/alloy/config.alloy"]
#bind to 0.0.0.0 and use port 12345 for the server, and set the storage path to /var/lib/alloy/data to pass cloud run's health checks and allow for persistent storage of the stock data snapshot.

