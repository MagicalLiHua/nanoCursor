package indexer

import "testing"

func TestClassifyFile(t *testing.T) {
	tests := []struct {
		path string
		want string
	}{
		{"src/main.py", "entry_point"},
		{"src/app.py", "entry_point"},
		{"tests/test_foo.py", "test"},
		{"src/test_utils.py", "test"},
		{"config.yaml", "config"},
		{"settings.json", "config"},
		{"README.md", "doc"},
		{"src/handler.py", "source"},
		{"src/utils.js", "source"},
	}
	for _, tt := range tests {
		got := classifyFile(tt.path)
		if got != tt.want {
			t.Errorf("classifyFile(%q) = %q, want %q", tt.path, got, tt.want)
		}
	}
}

func TestDetectLanguage(t *testing.T) {
	tests := []struct {
		ext  string
		want string
	}{
		{".py", "python"},
		{".js", "javascript"},
		{".ts", "typescript"},
		{".jsx", "javascript"},
		{".json", "json"},
		{".yaml", "yaml"},
		{".md", "text"},
	}
	for _, tt := range tests {
		got := detectLanguage(tt.ext)
		if got != tt.want {
			t.Errorf("detectLanguage(%q) = %q, want %q", tt.ext, got, tt.want)
		}
	}
}
