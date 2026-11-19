// Cypress test for Phase 1 Temporal Edge UI
// Checks that the graph loads and the legend contains Temporal edges

describe('Temporal Edge UI', () => {
  it('loads the app and shows temporal edge legend', () => {
    // Visit dev server (assume running on localhost:5173)
    cy.visit('http://localhost:5173')
    // Wait for graph to render
    cy.get('[data-testid="graph-canvas"]').should('exist')
    // Open scope selector and choose "Temporal Evidence"
    cy.get('[data-testid="scope-selector"]').click()
    cy.contains('Temporal Evidence').click()
    // Verify legend contains supports and contradicts entries
    cy.get('[data-testid="edge-legend"]').within(() => {
      cy.contains('supports')
      cy.contains('contradicts')
      cy.contains('precedes')
    })
  })
})

