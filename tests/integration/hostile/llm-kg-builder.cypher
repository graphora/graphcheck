CREATE
  (document:Document {
    fileName: 'Untitled Diagram.png',
    fileSource: 'local file',
    fileType: 'png',
    status: 'Completed',
    model: 'openai_gpt_4.5',
    nodeCount: 9,
    processingTime: 15.91,
    createdAt: '2025-04-10T16:33:22.331776000'
  }),
  (chunk1:Chunk {
    id: 'chunk-0001',
    text: 'Ada Lovelace worked with the Analytical Engine in the United Kingdom.',
    position: 1,
    embedding: [0.125, -0.25, 0.5, 0.0]
  }),
  (chunk2:Chunk {
    id: 'chunk-0002',
    text: 'The same source inconsistently calls the region UK and United Kingdom.',
    position: 'second',
    embedding: [0.126, -0.249, 0.501, 0.001]
  }),
  (ada:__Entity__:Person {
    id: 'Ada Lovelace',
    name: 'Ada Lovelace',
    age: 37,
    aliases: ['Augusta Ada King', 'Ada'],
    description: 'Mathematician'
  }),
  (adaDuplicate:__Entity__:person {
    id: 'ada_lovelace',
    name: 'ADA LOVELACE',
    age: 'unknown',
    confidence: 0.61
  }),
  (engine:__Entity__:`Machine / Concept` {
    id: 'Analytical Engine',
    `display name`: 'Analytical Engine',
    invented: '1837-ish',
    confidence: 'high'
  }),
  (uk:__Entity__:Country:Location:`Country / Region` {
    id: 'United Kingdom',
    `display name`: 'United Kingdom',
    description: '',
    confidence: 0.98
  }),
  (ukDuplicate:__Entity__:Location {
    id: 'UK',
    `display name`: 'U.K.',
    confidence: '0.72'
  }),
  (escaped:`Odd``Label` {
    id: 'escaped-identifier',
    `tick``key`: 'preserved',
    `property with spaces`: 'also preserved',
    tags: ['noisy', 'llm-output']
  }),
  (document)-[:FIRST_CHUNK]->(chunk1),
  (document)-[:PART_OF]->(chunk1),
  (document)-[:PART_OF]->(chunk2),
  (chunk1)-[:NEXT_CHUNK {distance: 1}]->(chunk2),
  (chunk1)-[:HAS_ENTITY {rank: 1}]->(ada),
  (chunk1)-[:HAS_ENTITY {rank: 'first'}]->(engine),
  (chunk1)-[:HAS_ENTITY]->(uk),
  (chunk2)-[:HAS_ENTITY]->(adaDuplicate),
  (chunk2)-[:HAS_ENTITY]->(ukDuplicate),
  (ada)-[:`WORKED-WITH` {source: 'chunk-0001'}]->(engine),
  (ada)-[:LOCATED_IN]->(uk),
  (escaped)-[:`points to`]->(ukDuplicate)
