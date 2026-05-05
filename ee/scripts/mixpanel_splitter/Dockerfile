FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -ldflags='-s -w' -o /out/splitter .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/splitter /splitter
ENTRYPOINT ["/splitter"]
