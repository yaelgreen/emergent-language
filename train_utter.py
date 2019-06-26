import os
import random

import numpy as np
import pandas as pd
import torch
from torch.autograd import Variable

import configs
from modules import data, action
from modules.action import ActionModule
from modules.agent import AgentModule
from modules.game import GameModule
from modules.predefined_utterances_module import PredefinedUtterancesModule
from modules.utterance import Utterance
from train import parser


def main():
    args = vars(parser.parse_args())
    mode = args['mode']
    if mode == 'selfplay':
        selfplay = True
    else:
        selfplay = False
    one_sentence_mode = args['one_sentence_data_set']
    run_default_config = configs.get_run_config(args)
    folder_dir = run_default_config.folder_dir
    agent_config = configs.get_agent_config(args)
    game_config = configs.get_game_config(args)
    utterance_config = configs.get_utterance_config()
    training_config = configs.get_training_config(args, folder_dir)
    corpus = data.WordCorpus('data' + os.sep, freq_cutoff=20, verbose=True)
    agent = AgentModule(agent_config, utterance_config, corpus, run_default_config.creating_data_set_mode,
                        run_default_config.create_utterance_using_old_code)
    utter = Utterance(agent_config.action_processor, utterance_config, corpus, run_default_config.create_utterance_using_old_code)
    if not mode == "train_utter":
        folder_dir_fb_model = utterance_config.fb_dir
        with open(folder_dir_fb_model, 'rb') as f:
            utter.load_state_dict(torch.load(f))
    action = ActionModule(agent_config.action_processor, utterance_config, corpus, run_default_config.create_utterance_using_old_code)
    create_data_set = PredefinedUtterancesModule()
    if one_sentence_mode:
        num_agents = np.random.randint(game_config.min_agents,
                                       game_config.max_agents + 1)
        num_landmarks = np.random.randint(game_config.min_landmarks,
                                          game_config.max_landmarks + 1)
        agent.reset()
        game = GameModule(game_config, num_agents, num_landmarks, folder_dir)
        df_utterance = [pd.DataFrame(index=range(game.batch_size), columns=agent.df_utterance_col_name
                                     , dtype=np.int64) for i in range(game.num_agents)]
        iter = random.randint(0, game.time_horizon)
        df_utterance = create_data_set.generate_sentences(game, iter, df_utterance, one_sentence_mode, mode=mode)
    for epoch in range(training_config.num_epochs):
        if not one_sentence_mode:
            num_agents = np.random.randint(game_config.min_agents,
                                       game_config.max_agents + 1)
            num_landmarks = np.random.randint(game_config.min_landmarks,
                                          game_config.max_landmarks + 1)
            agent.reset()
            game = GameModule(game_config, num_agents, num_landmarks, folder_dir)
            df_utterance = [pd.DataFrame(index=range(game.batch_size), columns=agent.df_utterance_col_name
                                          ,dtype=np.int64) for i in range(game.num_agents)]
            iter = random.randint(0, game.time_horizon)
            df_utterance = create_data_set.generate_sentences(game, iter, df_utterance, one_sentence_mode, mode=mode)
        agent_num = random.randint(0, game.num_agents-1)
        physical_feat = agent.get_physical_feat(game, agent_num)
        mem = Variable(torch.zeros(game.batch_size, game.num_agents,game_config.memory_size)[:, agent_num])
        utterance_feat = torch.zeros([game.batch_size, 1, 256], dtype=torch.float)
        goal = game.observed_goals[:, agent_num]
        processed, mem = action.processed_data(physical_feat, goal, mem,
                                                   utterance_feat)
        if selfplay and one_sentence_mode:
           processed = torch.load(args['folder_dir']+os.sep+'processed.pt')
        elif not selfplay and one_sentence_mode:
            torch.save(processed, args['folder_dir']+os.sep+'processed.pt')
        full_sentence = df_utterance[agent_num]['Full Sentence' + str(iter)]
        if selfplay:
            loss, utterance, optimizer = utter(processed, full_sentence, epoch=epoch)
        else:
            loss, utterance, optimizer = utter(processed, full_sentence, epoch=epoch)
    if mode == 'train_utter':
        with open(training_config.save_model_file, 'wb') as f:
                torch.save(utter.state_dict(), f)
    print("Saved agent model weights at %s" % training_config.save_model_file)
        # model_state = {'epoch': epoch + 1, 'state_dict': utter.state_dict(),
        #      'optimizer': optimizer.state_dict()}
        # torch.save(model_state, training_config.save_model_file)

if __name__ == "__main__":
    main()
