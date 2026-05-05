FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY go.mod main.go ./
# `go mod tidy` materialises go.sum on the fly so we don't have to commit it
# (and risk drift from the laptop that ran tidy). Cloud Build allows the
# outbound traffic to proxy.golang.org / sum.golang.org needed for this.
RUN go mod tidy && CGO_ENABLED=0 go build -ldflags='-s -w' -o /out/splitter .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/splitter /splitter
ENTRYPOINT ["/splitter"]
