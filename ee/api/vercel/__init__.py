"""FOSS stub for ee.api.vercel.

posthog/api/__init__.py:116 does:
    from ee.api.vercel import vercel_installation, vercel_product, vercel_proxy, vercel_resource
and uses each as a module with .VercelXxxxViewSet attributes -- BUT only
inside an `if EE_AVAILABLE:` block (lines 830-849). With EE_AVAILABLE=False
those registrations don't run, so the names just need to be import-resolvable.

We expose four trivial submodules below via Python's normal import machinery
(four sibling files in this directory).
"""
