# Use the base image from tiangolo/nginx-rtmp
FROM tiangolo/nginx-rtmp:latest

LABEL maintainer="Ken Dresdell <kdresdell@gmail.com>"
LABEL description="NGINX-RTMP server for IPTV restreaming"

# Create folders
RUN mkdir -p /nginx
RUN mkdir -p /nginx/hls /nginx/html /nginx/log /nginx/recordings

# Copy your nginx.conf file to the container
COPY nginx.conf /etc/nginx/nginx.conf

# Set permissions first
RUN chmod 777 /nginx/hls /nginx/html /nginx/log /nginx/recordings

# Copy web content
COPY index.html /nginx/html/index.html
COPY live.html /nginx/html/live.html
RUN chmod 644 /nginx/html/index.html /nginx/html/live.html

# Install additional tools
RUN apt-get update && \
    apt-get install -y curl nano iproute2 && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /nginx

# Expose the RTMP and HTTP ports
EXPOSE 1935
EXPOSE 8080
EXPOSE 8081

# Start NGINX
CMD ["nginx", "-g", "daemon off;"]