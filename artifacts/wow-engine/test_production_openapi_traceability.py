def test_final_production_entrypoint_exposes_traceability_routes():
    import api_ncaaf_acceptance as production

    route_methods = {
        (route.path, method)
        for route in production.app.routes
        for method in (getattr(route, "methods", set()) or set())
    }
    assert ("/record-recommendations", "POST") in route_methods
    assert ("/settle-recommendations", "POST") in route_methods

    schema = production.app.openapi()
    assert schema["paths"]["/record-recommendations"]["post"]
    assert schema["paths"]["/settle-recommendations"]["post"]
