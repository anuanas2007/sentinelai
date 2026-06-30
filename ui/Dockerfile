FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Plain nginx serving the static build -- the standard, well-understood
# pattern for a built SPA. The browser (not this container) talks
# directly to sentinel-agent's published port for the API/SSE, so no
# proxying or runtime env injection is needed here.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
