package executor

type ProcessSpec struct {
	Kind           string
	Command        string
	Cwd            string
	TimeoutMS      int
	MaxStdoutChars int
	MaxStderrChars int
}

type StdioProcessSpec struct {
	Kind           string
	Command        string
	Args           []string
	Cwd            string
	Env            map[string]string
	MaxStderrChars int
}

type ProcessEvent struct {
	Type    string
	Payload map[string]any
}

type ProcessResult struct {
	Status          string
	ExitCode        int
	Stdout          string
	Stderr          string
	StdoutTruncated bool
	StderrTruncated bool
	StdoutBytes     int
	StderrBytes     int
	DurationMS      int64
	TimedOut        bool
	Error           string
}

type EventSink func(ProcessEvent)
