//go:build !vertex

package cmd

import (
	"fmt"

	"github.com/anthropics/anthropic-sdk-go/option"
)

// initVertexProvider is a stub that returns an error when the binary was built without
// Vertex AI support. Build with -tags=vertex to enable Vertex AI.
func initVertexProvider(region, projectID string) (option.RequestOption, error) {
	return nil, fmt.Errorf("vertex provider is not available in this build (build with -tags=vertex to enable)")
}
