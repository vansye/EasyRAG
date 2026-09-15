"""A's offline recovery capabilities preserve IDs only for proven matching chunks."""

import pytest

from app.modules.knowledge import public as knowledge
from tests.mysql_knowledge_integration import database, sql, whole


def test_snapshot_reports_stored_chunk_validity_and_excludes_deleted_documents(database):
    service, sandbox = database
    document = service.create('note.md', b'hello')
    chunks = service.begin_indexing(document.id, whole(document.content))
    service.mark_indexed(document.id)
    deleted = service.create('deleted.md', b'deleted')
    service.delete(deleted.id)
    snapshots = service.snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].document.id == document.id and snapshots[0].chunks == chunks
    assert snapshots[0].chunks_valid
    sql(sandbox, "UPDATE chunk SET text='wrong' WHERE id=%s", (chunks[0].id,))
    assert not service.snapshots()[0].chunks_valid


@pytest.mark.parametrize('status', ['INDEXING','INDEXED','FAILED','PENDING'])
def test_rebuild_can_resume_any_active_state_and_reuses_matching_ids(database,status):
    service, sandbox = database
    document = service.create('note.md', b'hello')
    chunks = service.begin_indexing(document.id, whole(document.content))
    sql(sandbox, "UPDATE document SET index_status=%s,updated_at='2000-01-01' WHERE id=%s", (status,document.id))
    before = service.get(document.id)
    recovered = service.begin_rebuild(document.id, whole(document.content), expected_content=document.content)
    assert recovered == chunks
    current = service.get(document.id)
    assert current.index_status == 'INDEXING' and current.updated_at == before.updated_at
    service.mark_indexed(document.id)


def test_rebuild_allocates_new_ids_when_current_split_configuration_changed(database):
    service, _ = database
    document = service.create('note.md', b'hello world')
    old = service.begin_indexing(document.id, whole(document.content))
    drafts = (knowledge.ChunkWrite(0,'hello ',0,6,'',1), knowledge.ChunkWrite(1,'world',6,11,'',1))
    current = service.begin_rebuild(document.id,drafts,expected_content=document.content)
    assert len(current) == 2 and {chunk.id for chunk in current}.isdisjoint({chunk.id for chunk in old})
    assert service.get(document.id).chunk_count == 2


def test_stale_rebuild_content_cannot_change_existing_state_or_ids(database):
    service, _ = database
    document = service.create('note.md', b'hello')
    old = service.begin_indexing(document.id,whole(document.content))
    service.mark_indexed(document.id)
    with pytest.raises(knowledge.IndexStateConflict):
        service.begin_rebuild(document.id,whole('stale'),expected_content='stale')
    assert service.chunks(document.id) == old and service.get(document.id).index_status == 'INDEXED'
