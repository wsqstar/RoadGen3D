#!/usr/bin/env python3
"""Test script for the new generation API.

This verifies that the API structure is correct without requiring
full scene generation (which needs torch and other heavy deps).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_PATH = ROOT / "src"
sys.path.insert(0, str(SRC_PATH))

from roadgen3d.services.generation_core import (
    MetaurbanDesignParams,
    TemplateDesignParams,
    GenerationOptions,
)

def test_params_creation():
    """Test that design params can be created."""
    print("Testing MetaurbanDesignParams...")
    params = MetaurbanDesignParams(
        reference_plan_id="hkust_gz_gate",
        lane_count=2,
        lane_width_m=3.5,
        sidewalk_width_m=2.5,
        seed=42,
    )
    assert params.reference_plan_id == "hkust_gz_gate"
    assert params.lane_count == 2
    print(f"✓ MetaurbanDesignParams created: {params}")
    
    print("\nTesting TemplateDesignParams...")
    template_params = TemplateDesignParams(
        template_id="test_template",
        lane_count=2,
        length_m=80.0,
    )
    assert template_params.template_id == "test_template"
    print(f"✓ TemplateDesignParams created: {template_params}")


def test_options_creation():
    """Test that generation options can be created."""
    print("\nTesting GenerationOptions...")
    options = GenerationOptions()
    print(f"✓ GenerationOptions created")
    print(f"  - manifest_path: {options.manifest_path}")
    print(f"  - out_dir: {options.out_dir}")
    print(f"  - device: {options.device}")


def test_fastapi_router():
    """Test that FastAPI router is properly configured."""
    print("\nTesting FastAPI router...")
    from roadgen3d.services.generation_api import router
    
    # Check router has the expected routes
    route_paths = [route.path for route in router.routes]
    expected_paths = [
        "/designs/metaurban",
        "/designs/template",
        "/designs/osm",
        "/designs/{job_id}/status",
        "/scenes/{job_id}",
    ]
    
    for expected in expected_paths:
        assert expected in route_paths, f"Missing route: {expected}"
        print(f"  ✓ Route found: {expected}")
    
    print("✓ All expected routes are present")


def test_ui_app():
    """Test that the main UI app includes the generation router."""
    print("\nTesting UI FastAPI app...")
    from ui.api import app
    
    # Check app has generation routes
    route_paths = [route.path for route in app.routes]
    expected_generation_paths = [
        "/api/designs/metaurban",
        "/api/designs/template",
        "/api/designs/osm",
        "/api/designs/{job_id}/status",
        "/api/scenes/{job_id}",
    ]
    
    for expected in expected_generation_paths:
        assert expected in route_paths, f"Missing API route: {expected}"
        print(f"  ✓ API route found: {expected}")
    
    # Check CORS middleware
    middleware_names = []
    for m in app.user_middleware:
        if hasattr(m, 'cls'):
            middleware_names.append(m.cls.__name__)
        else:
            middleware_names.append(type(m).__name__)
    
    assert "CORSMiddleware" in middleware_names, f"CORS middleware not found. Found: {middleware_names}"
    print(f"  ✓ CORS middleware configured")
    
    print("✓ UI app is properly configured")


def main():
    """Run all tests."""
    print("=" * 60)
    print("RoadGen3D Generation API Tests")
    print("=" * 60)
    
    try:
        test_params_creation()
        test_options_creation()
        test_fastapi_router()
        test_ui_app()
        
        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("=" * 60)
        print("\nThe new generation API is correctly structured.")
        print("Note: Actual scene generation requires torch and asset manifests.")
        print("\nNext steps:")
        print("1. Start the API server: uvicorn ui.api:app --port 8000")
        print("2. Call POST /api/designs/metaurban with parameters")
        print("3. Poll GET /api/designs/{job_id}/status for completion")
        print("4. Access the viewer URL from the result")
        return 0
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
