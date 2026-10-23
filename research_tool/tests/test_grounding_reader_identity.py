"""A report reader is part of grounded research identity, unlike post-run refresh."""
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.tests.test_nine_loop_runner import config


def initialized(tmp_path, module):
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module
    pipe = ResearchPipeline(cfg)
    pipe.topic = cfg.topic
    pipe.root = tmp_path / 'battery'
    from research_tool.nine_loop.runtime_state import RuntimeState
    pipe.runtime = RuntimeState(pipe.root)
    pipe._initialize()
    return pipe._state['input_fingerprint']


def test_changing_reader_content_or_path_changes_grounded_identity(tmp_path):
    module = tmp_path / 'reader'
    (module / 'researchctl/layouts').mkdir(parents=True)
    (module / 'researchctl/cli.py').write_text('reader=1')
    (module / 'researchctl/layouts/evidence_snapshot.py').write_text('view=1')
    first = initialized(tmp_path, module)
    (module / 'researchctl/layouts/evidence_snapshot.py').write_text('view=2')
    assert initialized(tmp_path, module) != first


def test_same_named_interpreter_replacement_changes_reader_identity(tmp_path):
    from research_tool.infrastructure.retrieval import ResearchCtlClient
    module = tmp_path / 'reader'
    (module / 'researchctl').mkdir(parents=True)
    (module / 'researchctl/cli.py').write_text('reader=1')
    wrapper = tmp_path / 'python-wrapper'
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n')
    wrapper.chmod(0o700)
    reader = ResearchCtlClient(tmp_path, module_root=module, python_executable=str(wrapper))
    first = reader.reader_identity()
    wrapper.write_text('#!/bin/sh\n# changed runtime wrapper\nexec /usr/bin/python3 "$@"\n')
    assert reader.reader_identity() != first


def test_interpreter_invocation_preserves_venv_path_not_only_resolved_target(tmp_path):
    import sys
    from research_tool.infrastructure.retrieval import ResearchCtlClient
    module = tmp_path / 'reader'
    (module / 'researchctl').mkdir(parents=True)
    (module / 'researchctl/cli.py').write_text('reader=1')
    env = tmp_path / 'venv'
    (env / 'bin').mkdir(parents=True)
    executable = env / 'bin/python'
    executable.symlink_to(sys.executable)
    (env / 'pyvenv.cfg').write_text('home = fixture\ninclude-system-site-packages = false\n')
    reader = ResearchCtlClient(tmp_path, module_root=module, python_executable=str(executable))
    first = reader.reader_identity()
    assert reader._command('capabilities', [])[0] == str(executable)
    (env / 'pyvenv.cfg').write_text('home = changed\ninclude-system-site-packages = false\n')
    assert reader.reader_identity() != first
