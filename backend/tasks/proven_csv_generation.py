#!/usr/bin/env python3

import os
import csv
import time
import re
from celery import shared_task
from backend.utils.bulk_csv_generator import ContentRow
from backend.utils.llm_service import get_default_llm, ChatMessage, MessageRole
from backend.utils.unified_progress_system import get_unified_progress, ProcessType, ProcessStatus
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def generate_proven_csv_task(self, topics, output_filename, client_name, project_name, website, target_website, target_word_count, job_id, model_name=None):
    try:
        logger.info(f"Starting proven CSV generation: {len(topics)} topics for {client_name}")
        
        progress_system = None
        progress_id = None
        try:
            progress_system = get_unified_progress()
            progress_id = job_id
            created_id = progress_system.create_process(
                ProcessType.FILE_GENERATION,
                f"Proven CSV Generation - {client_name}",
                {"job_id": job_id, "total_items": len(topics)}
            )
            logger.info(f"Progress tracking initialized: progress_id={progress_id}, created_id={created_id}")
        except Exception as e:
            logger.warning(f"Progress system initialization failed: {e}")
            progress_system = None
            progress_id = None
        
        if model_name:
            logger.info(f"Using specified model: {model_name}")
            from llama_index.llms.ollama import Ollama
            from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT
            timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)
            llm = Ollama(model=model_name, base_url=OLLAMA_BASE_URL, request_timeout=timeout_value)
        else:
            logger.info("Using default/active model")
            llm = get_default_llm()
        
        def get_category_from_topic(topic):
            topic_lower = topic.lower()
            if any(term in topic_lower for term in ['actos', 'drug', 'medication', 'pharmaceutical']):
                return 'Pharmaceutical Law'
            elif any(term in topic_lower for term in ['malpractice', 'medical', 'doctor', 'hospital']):
                return 'Medical Malpractice'
            elif any(term in topic_lower for term in ['wrongful death', 'death', 'fatality']):
                return 'Wrongful Death'
            elif any(term in topic_lower for term in ['birth', 'cerebral palsy', 'delivery']):
                return 'Birth Injury'
            elif any(term in topic_lower for term in ['work', 'compensation', 'workplace']):
                return 'Workers Compensation'
            else:
                return 'Personal Injury'

        def generate_content(topic, page_id):
            category = get_category_from_topic(topic)
            
            state = "Ohio"
            
            prompt = f'Write 400-500 words of professional legal content about {topic} for {client_name} in {state}. Use HTML tags: h1, h2, h3, p, strong, em, ul, li. Focus on helping {state} clients. Be detailed and professional.'
            
            try:
                messages = [
                    ChatMessage(role=MessageRole.SYSTEM, content='You are a legal content writer. Write detailed HTML content for law firms. Respond with only the HTML content.'),
                    ChatMessage(role=MessageRole.USER, content=prompt),
                ]
                response = llm.chat(messages)
                
                if response and hasattr(response, 'message') and response.message:
                    try:
                        content = response.message.content.strip()
                    except (ValueError, AttributeError):
                        blocks = getattr(response.message, 'blocks', [])
                        content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                        content = content.strip()
                    content = re.sub(r'^.*?<h1>', '<h1>', content, flags=re.DOTALL)
                    content = re.sub(r'```.*', '', content, flags=re.DOTALL)
                else:
                    content = f'<h1>{topic} - {client_name}</h1><p>Expert legal representation for {topic.lower()} in {state}.</p>'
                    
            except Exception as e:
                logger.error(f'Error generating content for {topic}: {e}')
                content = f'<h1>{topic} - {client_name}</h1><p>Professional legal services for {topic.lower()} in {state}.</p>'
            
            title = f'{topic} - {client_name}' if len(topic) < 50 else f'{topic[:47]}...'
            excerpt = f'Expert legal representation for {topic.lower()} in {state}. {client_name} provides experienced advocacy.'
            if len(excerpt) > 250:
                excerpt = excerpt[:247] + '...'
            
            slug = re.sub(r'[^a-zA-Z0-9\s-]', '', topic.lower())
            slug = re.sub(r'\s+', '-', slug.strip()).strip('-')[:50]
            if not slug:
                slug = f'page-{page_id}'
            
            tag_sets = {
                'Personal Injury': ['personal injury', 'accident lawyer', 'injury claims', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation'],
                'Medical Malpractice': ['medical malpractice', 'medical negligence', 'medical error', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation'],
                'Pharmaceutical Law': ['dangerous drugs', 'drug lawsuit', 'pharmaceutical litigation', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation'],
                'Wrongful Death': ['wrongful death', 'fatal accident', 'death lawsuit', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation'],
                'Birth Injury': ['birth injury', 'cerebral palsy', 'delivery complications', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation'],
                'Workers Compensation': ['workers compensation', 'workplace injury', 'work accident', 'legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'experienced', 'trusted', 'consultation']
            }
            default_tags = ['legal help', 'attorney', 'lawyer', state, 'compensation', 'legal representation', 'consultation', 'experienced', 'trusted', 'professional', 'expert', 'services']
            tag_list = tag_sets.get(category, default_tags)[:12]
            tags = ', '.join(tag_list)
            
            return ContentRow(
                id=f'{10000 + page_id:05d}',
                title=title,
                content=content,
                excerpt=excerpt,
                category=category,
                tags=tags,
                slug=slug,
                image=category
            )

        generated_rows = []
        batch_size = 25
        
        if progress_system and progress_id:
            progress_system.update_process(progress_id, 0, "Starting content generation...")
        
        for i in range(0, len(topics), batch_size):
            batch = topics[i:i+batch_size]
            batch_num = i//batch_size + 1
            total_batches = (len(topics) + batch_size - 1)//batch_size
            
            if progress_system and progress_id:
                progress_system.update_process(progress_id,
                    int((i / len(topics)) * 80),
                    f'Processing batch {batch_num}/{total_batches}'
                )
            
            for j, topic in enumerate(batch):
                page_id = i + j + 1
                try:
                    row = generate_content(topic, page_id)
                    generated_rows.append(row)
                    word_count = len(row.content.split())
                    logger.info(f'Generated {row.id}: {topic[:30]}... ({word_count} words)')
                except Exception as e:
                    logger.error(f'ERROR: {topic}: {e}')
            
            if i + batch_size < len(topics):
                time.sleep(1)

        logger.info(f'Generated {len(generated_rows)} pages successfully')

        if progress_system and progress_id:
            progress_system.update_process(progress_id, 85, "Writing CSV file...")

        csv_data = []
        for row in generated_rows:
            csv_data.append({
                'ID': row.id,
                'Title': row.title,
                'Content': row.content,
                'Excerpt': row.excerpt,
                'Category': row.category,
                'Tags': row.tags,
                'slug': row.slug,
                'Image': row.image
            })

        output_dir = os.environ.get('GUAARDVARK_OUTPUT_DIR')
        if not output_dir:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            output_dir = os.path.join(project_root, 'data', 'outputs')
        
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, output_filename)
        
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['ID', 'Title', 'Content', 'Excerpt', 'Category', 'Tags', 'slug', 'Image']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for row_data in csv_data:
                writer.writerow(row_data)

        total_words = sum(len(row.content.split()) for row in generated_rows)
        categories = {}
        for row in generated_rows:
            categories[row.category] = categories.get(row.category, 0) + 1

        stats = {
            'pages': len(generated_rows),
            'total_words': total_words,
            'avg_words_per_page': total_words // len(generated_rows) if generated_rows else 0,
            'file_size_mb': os.path.getsize(output_path) / 1024 / 1024,
            'categories': categories
        }

        if progress_system and progress_id:
            progress_system.update_process(progress_id, 100, f"Complete! Generated {len(generated_rows)} pages")
        
        logger.info(f'COMPLETE: {output_path} - {len(generated_rows)} pages, {total_words:,} words')
        
        return {
            'status': 'success',
            'output_path': output_path,
            'stats': stats,
            'message': f'Successfully generated {len(generated_rows)} pages using proven method'
        }

    except Exception as e:
        logger.error(f"Error in proven CSV generation task: {e}", exc_info=True)
        if progress_system and progress_id:
            try:
                progress_system.error_process(progress_id, f"Error: {str(e)}")
            except Exception as progress_error:
                logger.warning(f"Could not update progress on error: {progress_error}")
        raise e
