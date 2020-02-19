
class SessionInfo(object):

    def __init__(self, engine):
       self._engine = engine

    def __get_transitions(self, track_items, timebase):
        items = list()
        for i in track_items:
            item = dict(
                name=i.name,
                duration=i.duration.ticks/timebase,
                start=i.start.ticks/timebase,
                end=i.end.ticks/timebase,
                mediaType=i.mediaType,
                speed=i.getSpeed(),
            )
            items.append(item)
        return items

    def __get_track_items(self, track_items, timebase):
        # import sgtk
        # import os
        # engine = sgtk.platform.current_engine()
        items = list()

        for i in track_items:
            clip_name = i.name
            
            getMediaPath_clip = i.projectItem.getMediaPath()
            canChangeMediaPath = i.projectItem.canChangeMediaPath()
            # videoComponents = i.projectItem.videoComponents
            # sym_link = i.projectItem.name.replace('.mov', '.rv') if '.mov' in i.projectItem.name else i.projectItem.name
            
            # # check if the clip name is a shotgun shot
            # filter_ = [['code', 'is', clip_name], ['sg_sequence','is', engine.context.entity]]
            # shot_exists = engine.shotgun.find('Shot', filter_, ['sg_cut_in', 'sg_cut_out', 'sg_cut_order', 'sg_cut_duration'])
            
            # # the clip video source it the publishedfile "symlink" for adobe
            
            # folder_name = os.path.basename(os.path.dirname(getMediaPath_clip))
            # sym_link = i.projectItem.name if folder_name == 'publish' else folder_name
            # filter_ = [['code', 'is', sym_link], ['project','is', engine.context.project]]
            # # version = engine.shotgun.find('Version', filter_, ['code','sg_first_frame', 'sg_last_frame', 'entity'])
            # # version = engine.shotgun.find('PublishedFile', filter_, ['code','sg_cut_in', 'sg_cut_out', 'entity'])
            # sym_link_entity = engine.shotgun.find('PublishedFile', filter_, ['code','sg_versions', 'entity', 'published_file_type'])

            # somethime could be that the symlink published is not a .mov but it's a folder published
            # if not sym_link_entity :
            #     filter_ = [['code', 'is', os.path.dirname(getMediaPath_clip)], ['project','is', engine.context.project]]
            #     sym_link_entity = engine.shotgun.find('PublishedFile', filter_, ['code','sg_versions', 'entity', 'published_file_type'])


            item = dict(
                # shot_exists = shot_exists,
                name=i.name,
                duration=i.duration.ticks/timebase,
                start=i.start.ticks/timebase,
                end=i.end.ticks/timebase,
                inPoint=i.inPoint.ticks/timebase,
                outPoint=i.outPoint.ticks/timebase,
                mediaType=i.mediaType,
                # sym_link_entity=sym_link_entity,
                source_path_clip=getMediaPath_clip,
                # canChangeMediaPath = canChangeMediaPath,
                # videoComponents=videoComponents,
                isSelected = i.isSelected(),
                speed=i.getSpeed(),
                isAdjustmentLayer=i.isAdjustmentLayer()
            )

            items.append(item)

        return items

    def __get_tracks(self, sequence_tracks, timebase):
        tracks = list()
        for t in sequence_tracks:
            track = dict(
                id=t.id,
                name=t.name,
                mediaType=t.mediaType,
                clips=self.__get_track_items(t.clips, timebase),
                transitions=self.__get_transitions(t.transitions, timebase),
                isMuted=t.isMuted()
            )
            tracks.append(track)
        return tracks

    def __get_sequences(self, project_sequences):
        sequences = list()
        for s in project_sequences:
            timebase = s.timebase
            sequence = dict(
                sequenceID=s.sequenceID,
                name=s.name,
                inPoint=s.getInPointAsTime().ticks/timebase,
                outPoint=s.getOutPointAsTime().ticks/timebase,
                timebase=s.timebase,
                zeroPoint=s.zeroPoint/timebase,
                end=s.end/timebase,
                videoTracks=self.__get_tracks(s.videoTracks, timebase),
                audioTracks=self.__get_tracks(s.audioTracks, timebase)
            )
            sequences.append(sequence)
        return sequences

    def get_info(self):
        session_info = list()
        for p in self._engine.adobe.app.projects:
            project = dict(
                documentID=p.documentID,
                name=p.name,
                path=p.path,
                sequences=self.__get_sequences(p.sequences),
                activeSequence=p.activeSequence
            )
            session_info.append(project)
        return session_info
