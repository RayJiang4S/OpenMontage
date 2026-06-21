from lib.pipeline_loader import list_pipelines, load_pipeline


def test_all_pipeline_manifests_validate() -> None:
    for name in list_pipelines():
        load_pipeline(name)
