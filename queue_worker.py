import os
import json
import traceback
import threading
from tts_generator import generate_long
import soundfile as sf


class QueueWorker:
    """Handles processing of multiple conversion items in a queue"""
    
    def __init__(self, app_instance, ui_callbacks=None):
        self.app = app_instance
        self.current_queue_index = -1
        self.ui_callbacks = ui_callbacks or {}
        
    def process_queue(self):
        """Process all items in the queue"""
        try:
            print("Starting queue processing...")
            
            # Update UI for queue processing start
            if 'start_conversion' in self.ui_callbacks:
                self.ui_callbacks['start_conversion']()
            
            # Process each item in the queue
            for i, queue_item in enumerate(self.app.queue_items):
                
                self.current_queue_index = i
                self.app.current_queue_index = i
                input_path = queue_item['input_file']
                output_path = queue_item['output_file']
                
                # Update queue item status to processing
                queue_item['status'] = '⚙️ Processing'
                if 'update_queue_item_status' in self.ui_callbacks:
                    self.ui_callbacks['update_queue_item_status'](i, '⚙️ Processing')
                
                print(f"Processing queue item {i+1}/{len(self.app.queue_items)}: {input_path}")
                
                # Set up for this queue item (always start fresh)
                self.app.input_path_var.set(input_path)
                self.app.output_path_var.set(output_path)
                self.app.sf_mode = 'w'
                self.app.start_chunk_idx = 0
                self.app._pull_resume_info()
                
                # Process this item using the app's convert worker
                try:
                    result = self.app.convert_worker.convert_file(input_path, output_path)
                    if result:
                        # If we get here without exception, the item completed successfully
                        queue_item['status'] = '✅ Completed'
                        if 'update_queue_item_status' in self.ui_callbacks:
                            self.ui_callbacks['update_queue_item_status'](i, '✅ Completed')
                    else:
                        queue_item['status'] = '⏸️ Paused'
                        if 'update_queue_item_status' in self.ui_callbacks:
                            self.ui_callbacks['update_queue_item_status'](i, '⏸️ Paused')
                        print(f"Queue item {i+1} paused")
                        break
                except Exception as e:
                    # Update queue item status to failed
                    queue_item['status'] = '❌ Failed'
                    if 'update_queue_item_status' in self.ui_callbacks:
                        self.ui_callbacks['update_queue_item_status'](i, '❌ Failed')
                    print(f"Queue item {i+1} failed")
                    # Stop processing on failure
                    break
                
                if self.app.app_state.is_aborted:
                    print("Queue processing aborted by user")
                    break
                
            # Update UI for queue completion
            if 'finish_queue_processing' in self.ui_callbacks:
                self.ui_callbacks['finish_queue_processing']()
            
        except Exception as e:
            # Ensure cleanup happens
            self.app.app_state.set_state(self.app.app_state.ERROR)
            self.app._cleanup_on_exit()
            traceback.print_exc()
            if 'error_conversion' in self.ui_callbacks:
                self.ui_callbacks['error_conversion'](str(e))
    
    