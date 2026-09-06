package tools

import (
	mcp "github.com/modelcontextprotocol/go-sdk/mcp"
)

// Register declares this server's tool surface.
func Register(server *mcp.Server) {
	server.AddTool(&mcp.Tool{
		Name:        "read_dashboard",
		Description: "Read one dashboard.",
	}, readDashboard)

	server.AddTool(&mcp.Tool{
		Name:        "delete_dashboard",
		Description: "Delete one dashboard.",
	}, deleteDashboard)
}
