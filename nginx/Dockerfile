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
RUN chmod 755 /nginx/hls /nginx/html /nginx/log /nginx/recordings

# Copy the same web content used by the Compose bind mount.
COPY html/ /nginx/html/
RUN chmod -R a+rX /nginx/html

# Set the working directory
WORKDIR /nginx

# Expose the RTMP and HTTP ports
EXPOSE 1935
EXPOSE 8080
EXPOSE 8081

# Start NGINX
CMD ["nginx", "-g", "daemon off;"]