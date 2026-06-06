package main

import (
	"log"
	"net/http"

	"nanocursor/go-runtime/internal/api"
	"nanocursor/go-runtime/internal/config"
)

func main() {
	cfg := config.FromEnv()
	server := api.NewServer(cfg)

	log.Printf("nanocursor-runtime listening on %s", cfg.Addr)
	if err := http.ListenAndServe(cfg.Addr, server.Routes()); err != nil {
		log.Fatal(err)
	}
}
