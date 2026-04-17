FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PATH="/app/node_modules/.bin:${PATH}"
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN npm run build
ENV PORT=3000
EXPOSE 3000
CMD ["sh", "-c", "node node_modules/next/dist/bin/next start -H :: -p ${PORT:-3000}"]
