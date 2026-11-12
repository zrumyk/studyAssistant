from study.runtime import container_packages


def test_opencv_runtime_uses_headless_variant_and_preserves_version():
    assert container_packages('python',['numpy','opencv-python==4.10.0.84'])==['numpy','opencv-python-headless==4.10.0.84']
    assert container_packages('python',['opencv-contrib-python'])==['opencv-contrib-python-headless']
    assert container_packages('python',['opencv-python','opencv-python-headless'])==['opencv-python-headless']


def test_other_runtime_packages_are_not_substituted():
    assert container_packages('python',['matplotlib'])==['matplotlib']
    assert container_packages('javascript',['opencv-python'])==['opencv-python']
