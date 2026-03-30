//go:build vertex

package cmd

import (
	"context"
	"fmt"

	"github.com/anthropics/anthropic-sdk-go/option"
	"github.com/anthropics/anthropic-sdk-go/vertex"
)

// initVertexProvider sets up Vertex AI authentication using Google Application Default Credentials.
func initVertexProvider(region, projectID string) (opt option.RequestOption, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("vertex auth failed: %v", r)
		}
	}()
	ctx := context.Background()
	opt = vertex.WithGoogleAuth(ctx, region, projectID)
	return
}
