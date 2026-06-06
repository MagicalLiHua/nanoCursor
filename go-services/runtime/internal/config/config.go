package config

import "os"

type Config struct {
	Addr    string
	Version string
}

func FromEnv() Config {
	addr := os.Getenv("NANOCURSOR_GO_RUNTIME_ADDR")
	if addr == "" {
		addr = "127.0.0.1:8120"
	}
	return Config{
		Addr:    addr,
		Version: "0.1.0",
	}
}
